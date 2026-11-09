# audio_engine/library.py
"""Everything that reads/writes the library DB: indexing, people, tags, links, suggestions."""
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .analysis import analyze_file
from .config import AUDIO_EXTENSIONS
from .db import connect, init_db

log = logging.getLogger(__name__)

# ---- suggestion scoring (tweak freely) ----
MATCH_WEIGHT = 4.0  # per shared tag between a track and a person
TIER_WEIGHT = {1: 3.0, 2: 2.0, 3: 1.0, 4: 1.5}  # tier 5 (blacklist) and Inactive people are never suggested

TAG_ALIASES = {"r&b": "rnb", "r and b": "rnb", "hiphop": "hip hop", "rap": "hip hop"}


# ---------- small helpers ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _session(conn: Optional[sqlite3.Connection] = None, db_path: Optional[Path] = None):
    """Use the caller's connection if given (they commit); otherwise open one, commit, close."""
    if conn is not None:
        yield conn
        return
    own = connect(db_path)
    try:
        yield own
        own.commit()
    except Exception:
        own.rollback()
        raise
    finally:
        own.close()


def normalize_tag(tag: str) -> str:
    t = re.sub(r"[-_]+", " ", str(tag).strip().lower())
    t = " ".join(t.split())
    return TAG_ALIASES.get(t, t)


def _normalize_tags(tags: Optional[Iterable[str]]) -> List[str]:
    seen = []
    for tag in tags or []:
        t = normalize_tag(tag)
        if t and t not in seen:
            seen.append(t)
    return seen


def _split_genre(genre: Optional[str]) -> List[str]:
    return [p for p in re.split(r"[,/;]", genre or "") if p.strip()]


def get_track(track_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    with _session(conn) as db:
        row = db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return dict(row) if row else None


# ---------- indexing ----------

def index_file(path, deep: bool = True, force: bool = False,
               conn: Optional[sqlite3.Connection] = None) -> Tuple[int, str]:
    """
    Insert or update one track row. Returns (track_id, status) where status is
    'added', 'updated' or 'skipped' (unchanged file that was already analyzed).
    deep=False is a fast metadata-only pass (no BPM/key); a later deep run fills those in.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(p)
    stat = p.stat()

    with _session(conn) as db:
        row = db.execute("SELECT * FROM tracks WHERE path = ?", (str(p),)).fetchone()
        unchanged = bool(
            row and row["file_size"] == stat.st_size and abs((row["file_mtime"] or 0) - stat.st_mtime) < 1e-6
        )
        if unchanged and not force and (row["analyzed_at"] or not deep):
            return row["id"], "skipped"

        info = analyze_file(str(p), deep=deep)
        now = _now()
        bpm, key, camelot = info["bpm"], info["key"], info["camelot_key"]
        analyzed_at = now if deep else None
        if not deep and unchanged:  # quick pass shouldn't throw away earlier deep results
            bpm, key, camelot = row["bpm"], row["key"], row["camelot_key"]
            analyzed_at = row["analyzed_at"]

        values = (
            p.name, bpm, key, camelot, info["duration_sec"], info["format"],
            info["artist"], info["title"], info["album"], info["genre"],
            stat.st_size, stat.st_mtime, analyzed_at, now,
        )
        if row:
            db.execute(
                """UPDATE tracks SET filename=?, bpm=?, key=?, camelot_key=?, duration_sec=?, format=?,
                   artist=?, title=?, album=?, genre=?, file_size=?, file_mtime=?, analyzed_at=?, updated_at=?
                   WHERE id=?""",
                values + (row["id"],),
            )
            track_id, status = row["id"], "updated"
        else:
            cur = db.execute(
                """INSERT INTO tracks (filename, bpm, key, camelot_key, duration_sec, format,
                   artist, title, album, genre, file_size, file_mtime, analyzed_at, updated_at, path, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values + (str(p), now),
            )
            track_id, status = cur.lastrowid, "added"

        tag_track(track_id, _split_genre(info["genre"]), conn=db)  # file's genre tag becomes a track tag
        return track_id, status


def _iter_audio_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))  # skip hidden dirs (.Trash, .git ...)
        for name in sorted(filenames):
            if name.startswith("."):  # includes macOS '._' resource-fork files
                continue
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                yield Path(dirpath) / name


def index_folder(root_path, deep: bool = True, force: bool = False,
                 db_path: Optional[Path] = None) -> Dict[str, int]:
    """Walk a folder tree and index every audio file. Returns counts by status (+ 'failed')."""
    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    init_db(db_path)
    files = list(_iter_audio_files(root))
    total = len(files)
    log.info("Found %d audio files under %s", total, root)

    counts = {"added": 0, "updated": 0, "skipped": 0, "failed": 0}
    conn = connect(db_path)
    try:
        for i, file in enumerate(files, 1):
            try:
                _, status = index_file(file, deep=deep, force=force, conn=conn)
                conn.commit()
            except KeyboardInterrupt:
                conn.rollback()
                log.warning("Interrupted. Progress so far is saved; re-run to continue.")
                break
            except Exception as exc:
                conn.rollback()
                status = "failed"
                log.error("[%d/%d] FAILED %s: %s", i, total, file, exc)
            counts[status] += 1
            if status in ("added", "updated"):
                log.info("[%d/%d] %-7s %s", i, total, status, file)
            elif status == "skipped" and i % 100 == 0:
                log.info("[%d/%d] ... %d unchanged so far", i, total, counts["skipped"])
    finally:
        conn.close()
    return counts


# ---------- people ----------

def _person_key(name: str, email: Optional[str]) -> str:
    return (email or name).strip().lower()


def add_or_update_person(name: str, email: Optional[str] = None, tags_list: Optional[List[str]] = None, *,
                         role: Optional[str] = None, tier: Optional[int] = None,
                         location: Optional[str] = None, company: Optional[str] = None,
                         notes: Optional[str] = None, status: Optional[str] = None,
                         replace_tags: bool = False,
                         conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Insert or update a person; matched on email (if given) else name. Tags are merged with
    the existing ones unless replace_tags=True. Optional fields only overwrite when non-empty.
    Returns the person id.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    email = (email or "").strip() or None
    key = _person_key(name, email)
    new_tags = _normalize_tags(tags_list)

    with _session(conn) as db:
        row = db.execute("SELECT * FROM people WHERE person_key = ?", (key,)).fetchone()
        now = _now()
        if row is None:
            cur = db.execute(
                """INSERT INTO people (person_key, name, email, tags_json, role, tier, location, company,
                   notes, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, name, email, json.dumps(new_tags), role, tier, location, company, notes, status, now, now),
            )
            return cur.lastrowid

        tags = new_tags if replace_tags else _normalize_tags(json.loads(row["tags_json"]) + new_tags)
        db.execute(
            """UPDATE people SET name=?, email=?, tags_json=?, role=?, tier=?, location=?, company=?,
               notes=?, status=?, updated_at=? WHERE id=?""",
            (
                name, email or row["email"], json.dumps(tags),
                role or row["role"], tier if tier is not None else row["tier"],
                location or row["location"], company or row["company"],
                notes or row["notes"], status or row["status"], now, row["id"],
            ),
        )
        return row["id"]


# ---------- track tags ----------

def tag_track(track_id: int, tags_list: Iterable[str], conn: Optional[sqlite3.Connection] = None) -> List[str]:
    """Add tags to a track (duplicates ignored). Returns the track's full tag list."""
    with _session(conn) as db:
        for tag in _normalize_tags(tags_list):
            db.execute("INSERT OR IGNORE INTO track_tags (track_id, tag) VALUES (?, ?)", (track_id, tag))
        return [r["tag"] for r in db.execute(
            "SELECT tag FROM track_tags WHERE track_id = ? ORDER BY tag", (track_id,))]


# ---------- suggestions ----------

def record_send(track_id: int, person_id: int, note: Optional[str] = None,
                conn: Optional[sqlite3.Connection] = None) -> int:
    """Log that `track_id` was sent to `person_id`. Returns the send id."""
    with _session(conn) as db:
        cur = db.execute(
            "INSERT INTO sends (track_id, person_id, sent_at, note) VALUES (?,?,?,?)",
            (track_id, person_id, _now(), note),
        )
        return cur.lastrowid


def get_sends_for_track(track_id: int, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Everyone this track has been sent to, newest first."""
    with _session(conn) as db:
        rows = db.execute(
            """SELECT s.id, s.sent_at, s.note, p.id AS person_id, p.name, p.email
               FROM sends s JOIN people p ON p.id = s.person_id
               WHERE s.track_id = ? ORDER BY s.sent_at DESC, s.id DESC""", (track_id,))
        return [dict(r) for r in rows]


def suggest_recipients_for_track(track_id: int, top_n: int = 5, exclude_sent: bool = True,
                                 conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """
    Rank people for a track: +MATCH_WEIGHT per tag shared with the track, plus a small bonus
    for closer tiers. Inactive people, tier 5 (blacklist) and, unless exclude_sent=False,
    people who were already sent this track are skipped.
    Returns [{person_id, name, email, tier, score, matched_tags}, ...] best first.
    """
    with _session(conn) as db:
        track = db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if track is None:
            raise ValueError(f"No track with id {track_id}")
        track_tags = {r["tag"] for r in db.execute("SELECT tag FROM track_tags WHERE track_id = ?", (track_id,))}
        track_tags |= set(_normalize_tags(_split_genre(track["genre"])))

        already_sent = set()
        if exclude_sent:
            already_sent = {r["person_id"] for r in db.execute(
                "SELECT person_id FROM sends WHERE track_id = ?", (track_id,))}

        ranked = []
        for person in db.execute("SELECT * FROM people"):
            if person["id"] in already_sent:
                continue
            if person["tier"] == 5 or (person["status"] or "").strip().lower() == "inactive":
                continue
            matched = sorted(track_tags & set(json.loads(person["tags_json"])))
            score = MATCH_WEIGHT * len(matched) + TIER_WEIGHT.get(person["tier"], 0.5)
            ranked.append({
                "person_id": person["id"], "name": person["name"], "email": person["email"],
                "tier": person["tier"], "score": round(score, 2), "matched_tags": matched,
            })
        ranked.sort(key=lambda r: (-r["score"], r["tier"] or 99, r["name"].lower()))
        return ranked[:top_n]


# ---------- links ----------

def link_sample_to_track(sample_id: int, beat_id: int, relation_type: str = "uses_sample",
                         conn: Optional[sqlite3.Connection] = None) -> None:
    """Record that `beat_id` (a beat/demo/song) uses `sample_id`. Safe to call twice."""
    if sample_id == beat_id:
        raise ValueError("A track can't be linked to itself")
    with _session(conn) as db:
        db.execute(
            "INSERT OR IGNORE INTO track_links (parent_id, child_id, relation_type, created_at) VALUES (?,?,?,?)",
            (beat_id, sample_id, relation_type, _now()),
        )


def get_samples_for_track(track_id: int, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Samples linked to this beat/demo."""
    with _session(conn) as db:
        rows = db.execute(
            """SELECT t.*, l.relation_type FROM track_links l JOIN tracks t ON t.id = l.child_id
               WHERE l.parent_id = ? ORDER BY t.filename""", (track_id,))
        return [dict(r) for r in rows]


def get_tracks_using_sample(sample_id: int, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Beats/demos that use this sample."""
    with _session(conn) as db:
        rows = db.execute(
            """SELECT t.*, l.relation_type FROM track_links l JOIN tracks t ON t.id = l.parent_id
               WHERE l.child_id = ? ORDER BY t.filename""", (sample_id,))
        return [dict(r) for r in rows]
