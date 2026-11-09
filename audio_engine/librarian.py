# audio_engine/librarian.py
"""
The "librarian layer": guided import, non-destructive attribute editing with undo, saved searches.

Design rules
  * Audio files are never modified. Detected values live in `tracks`; user decisions live in `track_user`.
    Effective value = user value if set, else detected (see smart_search._index).
  * Every edit goes through apply_batch(), which logs old/new values so undo_batch() can restore them exactly.
  * Import is two steps: scan_folder() previews (writes nothing), start_import() does the work in a background thread.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import AUDIO_EXTENSIONS
from .db import connect, init_db
from .keys import to_camelot
from .library import _iter_audio_files, _normalize_tags, _now, _session, index_file
from . import smart_search

SAMPLE_TYPES = {"loop", "one-shot", "track"}
# editable field name -> track_user column
FIELD_COLUMNS = {"bpm": "bpm", "key": "camelot_key", "sample_type": "sample_type", "rating": "rating", "notes": "notes"}


# ====================================================================== import
def list_dirs(path: Optional[str] = None) -> Dict[str, Any]:
    """Folder picker backend: sub-folders of `path` (default: home) plus how many audio files sit directly in it."""
    p = Path(path).expanduser() if path else Path.home()
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    dirs, audio = [], 0
    try:
        with os.scandir(p) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(e.name)
                    elif os.path.splitext(e.name)[1].lower() in AUDIO_EXTENSIONS:
                        audio += 1
                except OSError:
                    continue
    except PermissionError:
        return {"path": str(p), "parent": str(p.parent), "dirs": [], "audio_here": 0, "error": "Permission denied"}
    shortcuts = [str(Path.home())] + [str(v) for v in sorted(Path("/Volumes").glob("*"))] if Path("/Volumes").is_dir() else [str(Path.home())]
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": sorted(dirs, key=str.lower)[:500],
            "audio_here": audio, "shortcuts": shortcuts}


def scan_folder(path: str, db_path=None) -> Dict[str, Any]:
    """Dry-run preview of an import. Writes nothing."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    init_db(db_path)
    conn = connect(db_path)
    try:
        known = {r[0] for r in conn.execute("SELECT path FROM tracks")}
    finally:
        conn.close()
    by_ext: Counter = Counter(); folders: Counter = Counter()
    total = new = size = 0
    examples: List[str] = []
    for f in _iter_audio_files(root):
        total += 1
        by_ext[f.suffix.lower().lstrip(".")] += 1
        try:
            size += f.stat().st_size
        except OSError:
            pass
        rel = f.relative_to(root)
        folders[rel.parts[0] if len(rel.parts) > 1 else "(top level)"] += 1
        if str(f.resolve()) not in known:
            new += 1
            if len(examples) < 6:
                examples.append(str(rel))
    return {"root": str(root), "total": total, "new": new, "already_in_library": total - new, "bytes": size,
            "by_format": dict(by_ext.most_common()), "top_folders": folders.most_common(8), "examples": examples}


class ImportJob:
    def __init__(self, path: str, deep: bool):
        self.id = uuid.uuid4().hex[:10]
        self.path, self.deep = path, deep
        self.state = "scanning"      # scanning -> importing -> tagging -> done | cancelled | error
        self.total = self.done = 0
        self.counts = {"added": 0, "updated": 0, "skipped": 0, "failed": 0}
        self.tag_stats: Dict[str, int] = {}
        self.error: Optional[str] = None
        self.started = time.time(); self.finished: Optional[float] = None
        self._cancel = threading.Event()

    def public(self) -> Dict[str, Any]:
        return {"id": self.id, "path": self.path, "deep": self.deep, "state": self.state, "total": self.total,
                "done": self.done, "counts": self.counts, "tag_stats": self.tag_stats, "error": self.error,
                "elapsed": round((self.finished or time.time()) - self.started, 1)}


_JOBS: Dict[str, ImportJob] = {}
_LOCK = threading.Lock()


def start_import(path: str, deep: bool = False) -> ImportJob:
    """Start (or return the already-running) background import. deep=True queues the sound analysis job afterwards."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    with _LOCK:
        for j in _JOBS.values():
            if j.state in ("scanning", "importing", "tagging"):
                return j
        job = ImportJob(str(root), deep)
        _JOBS[job.id] = job
    threading.Thread(target=_run_import, args=(job, root), daemon=True).start()
    return job


def get_job(job_id: str) -> Optional[ImportJob]:
    return _JOBS.get(job_id)


def cancel_job(job_id: str) -> bool:
    j = _JOBS.get(job_id)
    if j and j.state in ("scanning", "importing", "tagging"):
        j._cancel.set()
        return True
    return False


def _run_import(job: ImportJob, root: Path) -> None:
    from .search import autotag_library  # local import: search imports library, avoid cycles at module load
    try:
        init_db()
        files = list(_iter_audio_files(root))
        job.total = len(files); job.state = "importing"
        conn = connect()
        try:
            for i, f in enumerate(files, 1):
                if job._cancel.is_set():
                    job.state = "cancelled"; break
                try:
                    _, status = index_file(f, deep=False, conn=conn)   # fast metadata pass; sound analysis runs as its own job
                except Exception:
                    status = "failed"; conn.rollback()
                job.counts[status] += 1; job.done = i
                if i % 25 == 0:
                    conn.commit()
            conn.commit()
        finally:
            conn.close()
        if job.state != "cancelled":
            job.state = "tagging"
            job.tag_stats = autotag_library()
            try:
                from . import versions
                job.tag_stats["version_groups"] = versions.rebuild()["groups"]     # stack re-exports right away
            except Exception:
                pass
            if job.deep:                                    # user ticked "analyze audio": hand over to the resumable job
                from . import analysis_job
                analysis_job.start()
            job.state = "done"
    except Exception as exc:  # surface any failure to the UI instead of dying silently in the thread
        job.state, job.error = "error", str(exc)
    finally:
        job.finished = time.time()
        smart_search.invalidate()


# ====================================================================== batch edit + undo
def _clean_value(field: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if field == "bpm":
        v = float(value)
        if not 30 <= v <= 300:
            raise ValueError("BPM must be between 30 and 300")
        return round(v, 1)
    if field == "key":
        k = to_camelot(str(value))
        if not k:
            raise ValueError(f"'{value}' isn't a key I understand (try 8A, C# minor, F major)")
        return k
    if field == "sample_type":
        if value not in SAMPLE_TYPES:
            raise ValueError("type must be loop, one-shot or track")
        return value
    if field == "rating":
        v = int(value)
        if not 0 <= v <= 5:
            raise ValueError("rating must be 0-5")
        return v or None
    if field == "notes":
        return str(value)[:2000]
    raise ValueError(f"'{field}' is not an editable field")


def apply_batch(track_ids: Iterable[int], add_tags: Iterable[str] = (), remove_tags: Iterable[str] = (),
                set_fields: Optional[Dict[str, Any]] = None, clear_fields: Iterable[str] = (),
                collection: Optional[str] = None, label: Optional[str] = None,
                conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Edit many tracks at once. Validates everything first, then writes + logs in one transaction."""
    ids = list(dict.fromkeys(int(i) for i in track_ids))
    if not ids:
        raise ValueError("No tracks selected")
    add, rem = _normalize_tags(add_tags), _normalize_tags(remove_tags)
    sets = {f: _clean_value(f, v) for f, v in (set_fields or {}).items() if f in FIELD_COLUMNS or _raise(f)}
    clears = [f for f in clear_fields if f in FIELD_COLUMNS or _raise(f)]
    batch_id = uuid.uuid4().hex[:12]
    changed = 0
    with _session(conn) as db:
        ids = [r[0] for r in db.execute(f"SELECT id FROM tracks WHERE id IN ({','.join('?' * len(ids))})", ids)]
        db.execute("INSERT INTO edit_batches (batch_id, label, at, n_tracks) VALUES (?,?,?,?)",
                   (batch_id, label or _describe(add, rem, sets, clears, collection), _now(), len(ids)))

        def log(tid, field, old, new):
            db.execute("INSERT INTO edit_log (batch_id, track_id, field, old_json, new_json) VALUES (?,?,?,?,?)",
                       (batch_id, tid, field, json.dumps(old), json.dumps(new)))

        for tid in ids:
            touched = False
            have = {r[0] for r in db.execute("SELECT tag FROM track_tags WHERE track_id = ?", (tid,))}
            added = [t for t in add if t not in have]
            removed = [t for t in rem if t in have and t not in add]
            for t in added:
                db.execute("INSERT OR IGNORE INTO track_tags (track_id, tag) VALUES (?, ?)", (tid, t))
            for t in removed:
                db.execute("DELETE FROM track_tags WHERE track_id = ? AND tag = ?", (tid, t))
                db.execute("UPDATE auto_tags SET rejected = 1 WHERE track_id = ? AND tag = ?", (tid, t))   # never re-added by the app
            if added: log(tid, "tag+", None, added); touched = True
            if removed: log(tid, "tag-", removed, None); touched = True

            wanted = {**{f: None for f in clears}, **sets}
            if wanted:
                db.execute("INSERT OR IGNORE INTO track_user (track_id, updated_at) VALUES (?, ?)", (tid, _now()))
                cur = db.execute("SELECT * FROM track_user WHERE track_id = ?", (tid,)).fetchone()
                for field, value in wanted.items():
                    col = FIELD_COLUMNS[field]
                    if cur[col] != value:
                        db.execute(f"UPDATE track_user SET {col} = ?, updated_at = ? WHERE track_id = ?", (value, _now(), tid))
                        log(tid, col, cur[col], value); touched = True
            changed += touched
        if collection:
            from .search import add_to_collection
            already = {r[0] for r in db.execute(
                "SELECT ct.track_id FROM collection_tracks ct JOIN collections c ON c.id = ct.collection_id WHERE c.name = ?",
                (collection,))}
            add_to_collection(collection, ids, conn=db)
            for tid in ids:
                if tid not in already:
                    log(tid, "coll+", None, collection); changed += 1   # logged so undo can take it back out
    smart_search.invalidate()
    return {"batch_id": batch_id, "tracks": len(ids), "changed": changed}


def _raise(field):
    raise ValueError(f"'{field}' is not an editable field")


def _describe(add, rem, sets, clears, collection) -> str:
    parts = []
    if add: parts.append("tag +" + ",".join(add))
    if rem: parts.append("tag −" + ",".join(rem))
    parts += [f"{f}={v}" for f, v in sets.items()] + [f"reset {f}" for f in clears]
    if collection: parts.append(f"→ {collection}")
    return "; ".join(parts) or "edit"


def undo_batch(batch_id: str, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    with _session(conn) as db:
        b = db.execute("SELECT * FROM edit_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if b is None:
            raise ValueError("Unknown edit")
        if b["undone"]:
            raise ValueError("That edit was already undone")
        rows = db.execute("SELECT * FROM edit_log WHERE batch_id = ? ORDER BY id DESC", (batch_id,)).fetchall()
        for r in rows:
            old, new = json.loads(r["old_json"]), json.loads(r["new_json"])
            if r["field"] == "tag+":
                for t in new:
                    db.execute("DELETE FROM track_tags WHERE track_id = ? AND tag = ?", (r["track_id"], t))
            elif r["field"] == "coll+":
                db.execute("DELETE FROM collection_tracks WHERE track_id = ? AND collection_id = "
                           "(SELECT id FROM collections WHERE name = ?)", (r["track_id"], new))
            elif r["field"] == "tag-":
                for t in old:
                    db.execute("INSERT OR IGNORE INTO track_tags (track_id, tag) VALUES (?, ?)", (r["track_id"], t))
                    db.execute("UPDATE auto_tags SET rejected = 0 WHERE track_id = ? AND tag = ?", (r["track_id"], t))
            else:
                db.execute(f"UPDATE track_user SET {r['field']} = ?, updated_at = ? WHERE track_id = ?",
                           (old, _now(), r["track_id"]))
        db.execute("UPDATE edit_batches SET undone = 1 WHERE batch_id = ?", (batch_id,))
    smart_search.invalidate()
    return {"batch_id": batch_id, "reverted": len(rows)}


def recent_edits(limit: int = 20, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    with _session(conn) as db:
        return [dict(r) for r in db.execute(
            "SELECT batch_id, label, at, n_tracks, undone FROM edit_batches ORDER BY at DESC, rowid DESC LIMIT ?", (limit,))]


def delete_tag(tag: str, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Remove a tag from every track (undoable)."""
    from .library import normalize_tag
    t = normalize_tag(tag)
    with _session(conn) as db:
        ids = [r[0] for r in db.execute("SELECT track_id FROM track_tags WHERE tag = ?", (t,))]
    if not ids:
        raise ValueError(f"No track has the tag '{t}'")
    return apply_batch(ids, remove_tags=[t], label=f"delete tag “{t}”", conn=conn)


def rename_tag_logged(old: str, new: str, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Rename / merge a tag across the library (undoable)."""
    from .library import normalize_tag
    o, n = normalize_tag(old), normalize_tag(new)
    if not o or not n:
        raise ValueError("both tag names are required")
    with _session(conn) as db:
        ids = [r[0] for r in db.execute("SELECT track_id FROM track_tags WHERE tag = ?", (o,))]
    if not ids:
        raise ValueError(f"No track has the tag '{o}'")
    return apply_batch(ids, add_tags=[n], remove_tags=[o], label=f"rename tag “{o}” → “{n}”", conn=conn)


# ====================================================================== inspector + saved searches
def track_detail(track_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """Everything the inspector shows: detected vs effective values, tags, collections, edit history."""
    with _session(conn) as db:
        t = db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if t is None:
            return None
        u = db.execute("SELECT * FROM track_user WHERE track_id = ?", (track_id,)).fetchone()
        u = dict(u) if u else {}
        tags = [r[0] for r in db.execute("SELECT tag FROM track_tags WHERE track_id = ? ORDER BY tag", (track_id,))]
        colls = [r[0] for r in db.execute("SELECT c.name FROM collections c JOIN collection_tracks ct ON ct.collection_id = c.id "
                                          "WHERE ct.track_id = ? ORDER BY c.name", (track_id,))]
        hist = [dict(r) for r in db.execute(
            """SELECT b.label, b.at, b.undone, l.field FROM edit_log l JOIN edit_batches b ON b.batch_id = l.batch_id
               WHERE l.track_id = ? ORDER BY l.id DESC LIMIT 12""", (track_id,))]
        detected = {"bpm": t["bpm"], "key": t["camelot_key"] or to_camelot(t["key"]), "sample_type": t["sample_type"]}
        mine = {"bpm": u.get("bpm"), "key": u.get("camelot_key"), "sample_type": u.get("sample_type"),
                "rating": u.get("rating"), "notes": u.get("notes")}
        eff = {k: (mine[k] if mine[k] is not None else detected[k]) for k in detected}
        eff.update(rating=mine["rating"], notes=mine["notes"])
        autos = {r[0]: r[1] for r in db.execute("SELECT tag, source FROM auto_tags WHERE track_id = ? AND rejected = 0", (track_id,))}
        gm = db.execute("SELECT group_id FROM version_members WHERE track_id = ?", (track_id,)).fetchone()
        return {"id": t["id"], "filename": t["filename"], "path": t["path"], "format": t["format"],
                "auto_tags": {k: v for k, v in autos.items() if k in tags}, "group_id": gm[0] if gm else None,
                "duration_sec": t["duration_sec"], "bpm_source": t["bpm_source"], "detected": detected, "user": mine,
                "effective": eff, "tags": tags, "collections": colls, "history": hist}


def save_search(name: str, query: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the search a name")
    with _session(conn) as db:
        db.execute("INSERT INTO saved_searches (name, query_json, created_at) VALUES (?,?,?) "
                   "ON CONFLICT(name) DO UPDATE SET query_json = excluded.query_json", (name, json.dumps(query), _now()))
        return db.execute("SELECT id FROM saved_searches WHERE name = ?", (name,)).fetchone()[0]


def list_saved_searches(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    with _session(conn) as db:
        return [{"id": r["id"], "name": r["name"], "query": json.loads(r["query_json"])}
                for r in db.execute("SELECT * FROM saved_searches ORDER BY name")]


def delete_saved_search(search_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
    with _session(conn) as db:
        db.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
