# audio_engine/versions.py
"""
Version groups: "midnight drive 115.mp3" + "midnight drive 115.wav", "night shift V1" + "night shift FINAL V2", "open road" + "open road v2".

Two signals, both conservative:
  * SAME AUDIO  - fingerprint similarity >= 0.995, durations within 1.5 s, and at least 8 s long. (Calibrated on 49 real tracks: identical exports
                  scored 1.000, the most similar unrelated pair 0.718.)                         -> kind 'duplicate'
  * SAME TITLE  - the file name minus tempo, version words ("v2", "final", "master", "insert 3"), (brackets), the author's
                  own name and numbers is identical and long enough to be meaningful.            -> kind 'version'
Nothing is deleted or renamed. Groups only stack the list and make "sent" version-aware. Your decisions (set primary,
"not a version", merge) are locked and survive every re-scan.
"""
import re
import sqlite3
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from . import features as F, similarity as sim, smart_search
from .library import _now, _session

DUP_SCORE = 0.995
DUP_MIN_SECONDS = 8.0     # short sounds (one-shots, small loops) from one pack legitimately sound alike; never merge those on audio alone
DUP_DURATION_SLACK = 1.5
GENERIC = {"audio", "untitled", "idea", "ideas", "track", "beat", "beats", "sample", "samples", "new", "recording", "bounce",
           "mix", "loop", "loops", "drums", "vocal", "vox", "demo", "test", "song", "sketch", "freestyle", "master", "final"}
from .config import AUTHOR_WORDS as _USER_AUTHOR_WORDS            # your own name/handle: db/settings.json or AUDIO_LIBRARY_AUTHOR_WORDS
AUTHOR_WORDS = {"prod", "by", "x", "feat", "ft", "with", "and"} | _USER_AUTHOR_WORDS
_VERSION_WORDS = re.compile(r"\b(v\d+|final|master(ed)?|insert( ?\d+)?|demo|mix|bounce|copy|rough|alt|remix|instrumental|inst|"
                            r"acapella|stems?|clean|dirty|beat|starter|vox|vocals?|take ?\d+|version ?\d*)\b")


def stem(filename: str) -> str:
    """Title key for a file: lowercase, extension / tempo / version words / brackets / numbers / author words removed."""
    s = re.sub(r"\.[a-z0-9]{2,4}$", "", filename.lower())
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    s = re.sub(r"\d+(\.\d+)?\s*bpm", " ", s)
    # tempo-looking numbers (50-300, "115", "157.2") are noise; other numbers are part of the title ("1953" vs "1954", "song 2" vs "song 3")
    s = re.sub(r"(?<![\d.])\d{2,3}(\.\d+)?(?![\d])", lambda m: " " if 50 <= float(m.group(0)) <= 300 else m.group(0), s)
    s = re.sub(r"[_\-\.\+,!*#@']+", " ", s)
    s = _VERSION_WORDS.sub(" ", s)
    words = [w for w in s.split() if w not in AUTHOR_WORDS]
    return " ".join(words)


def valid_stem(st: str) -> bool:
    words = [w for w in st.split() if not w.isdigit()]
    letters = re.sub(r"[^a-z]", "", st)
    # 6+ letters: a bare collaborator name ("jane x joe 124 bpm") isn't a title
    return len(letters) >= 6 and st not in GENERIC and bool(words) and not all(w in GENERIC for w in words)


def primary_score(t: Dict[str, Any]) -> float:
    """Which file to show on top of a stack: lossless > high bitrate, 'final/master' in the name, newest."""
    fmt = (t.get("format") or "").lower()
    score = 3.0 if fmt in ("wav", "flac", "aiff", "aif") else 0.0
    if t.get("duration_sec") and t.get("file_size"):
        score += min(t["file_size"] * 8 / t["duration_sec"] / 320_000, 1.0) * 2
    name = t["filename"].lower()
    if re.search(r"\b(final|master)", name): score += 1.5
    if re.search(r"\b(demo|rough|insert|v1|idea)\b", name): score -= 0.7
    return score + (t.get("file_mtime") or 0) / 1e12        # newest breaks ties


class _UF:
    def __init__(self): self.p: Dict[int, int] = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b): self.p[self.find(a)] = self.find(b)


def _audio_pairs(db: sqlite3.Connection, dur: Dict[int, float]) -> List[tuple]:
    c = sim._load(db)
    if len(c["ids"]) < 2:
        return []
    Z, ids = c["Z"], c["ids"]
    w = sim.DEFAULT_WEIGHTS; tot = sum(w.values())
    out = []
    for start in range(0, len(ids), 512):
        block = slice(start, start + 512)
        with np.errstate(all="ignore"):   # spurious BLAS warnings on macOS (results verified exact)
            S = sum(w[g] * (Z[block, F.SLICES[g]] @ Z[:, F.SLICES[g]].T) for g in w) / tot
        rows, cols = np.where(S >= DUP_SCORE)
        for r, col in zip(rows, cols):
            a, b = int(ids[start + r]), int(ids[col])
            if a < b and (dur.get(a) or 0) >= DUP_MIN_SECONDS and abs((dur.get(a) or 0) - (dur.get(b) or 0)) <= DUP_DURATION_SLACK:
                out.append((a, b))
    return out


def rebuild(conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """Re-detect groups. Locked groups and 'not a version' exclusions are left exactly as the user set them."""
    with _session(conn) as db:
        tracks = {r["id"]: dict(r) for r in db.execute(
            "SELECT id, filename, duration_sec, file_size, file_mtime, format FROM tracks")}
        locked_members = {r[0] for r in db.execute("SELECT m.track_id FROM version_members m JOIN version_groups g "
                                                   "ON g.id = m.group_id WHERE g.locked = 1")}
        excluded = {r[0] for r in db.execute("SELECT track_id FROM version_exclusions")}
        free = {i for i in tracks if i not in locked_members and i not in excluded}
        db.execute("DELETE FROM version_groups WHERE locked = 0")           # members cascade
        uf, name_edges, audio_edges = _UF(), set(), set()

        buckets = defaultdict(list)
        for i in free:
            d = tracks[i]["duration_sec"]
            if d is not None and d < DUP_MIN_SECONDS:            # "kick 01", "kick 02": numbered one-shots are a series, not versions
                continue
            st = stem(tracks[i]["filename"])
            if valid_stem(st):
                buckets[st].append(i)
        for ids in buckets.values():
            for other in ids[1:]:
                uf.union(ids[0], other); name_edges.add(frozenset((ids[0], other)))
        dur = {i: tracks[i]["duration_sec"] for i in tracks}
        for a, b in _audio_pairs(db, dur):
            if a in free and b in free:
                uf.union(a, b); audio_edges.add(frozenset((a, b)))

        comps = defaultdict(list)
        for i in uf.p:
            comps[uf.find(i)].append(i)
        made = dup = ver = 0
        for members in comps.values():
            if len(members) < 2:
                continue
            ms = set(members)
            ua = _UF()
            for e in audio_edges:
                if e <= ms:
                    a, b = tuple(e); ua.union(a, b)
            same_audio = len({ua.find(m) for m in members}) == 1      # identical audio links every member (e.g. mp3 + wav of one export)
            kind = "duplicate" if same_audio else "version"
            conf = 0.99 if same_audio else (0.85 if any(e <= ms for e in audio_edges) else 0.6)
            primary = max(members, key=lambda i: primary_score(tracks[i]))
            gid = db.execute("INSERT INTO version_groups (primary_id, kind, confidence, locked, created_at) VALUES (?,?,?,0,?)",
                             (primary, kind, conf, _now())).lastrowid
            db.executemany("INSERT INTO version_members (track_id, group_id) VALUES (?,?)", [(m, gid) for m in members])
            made += 1; dup += kind == "duplicate"; ver += kind == "version"
    smart_search.invalidate()
    return {"groups": made, "duplicates": dup, "versions": ver}


def group_detail(group_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    with _session(conn) as db:
        g = db.execute("SELECT * FROM version_groups WHERE id = ?", (group_id,)).fetchone()
        if g is None:
            return None
        rows = db.execute("SELECT t.id, t.filename, t.path, t.format, t.duration_sec, t.file_size, t.bpm FROM version_members m "
                          "JOIN tracks t ON t.id = m.track_id WHERE m.group_id = ?", (group_id,)).fetchall()
        members = []
        for r in rows:
            kbps = round(r["file_size"] * 8 / r["duration_sec"] / 1000) if r["file_size"] and r["duration_sec"] else None
            members.append({**dict(r), "kbps": kbps, "is_primary": r["id"] == g["primary_id"],
                            "sent_count": db.execute("SELECT COUNT(*) FROM sends WHERE track_id = ?", (r["id"],)).fetchone()[0]})
        members.sort(key=lambda m: (not m["is_primary"], m["filename"].lower()))
        return {"id": g["id"], "kind": g["kind"], "confidence": g["confidence"], "locked": bool(g["locked"]),
                "primary_id": g["primary_id"], "members": members}


def list_groups(offset: int = 0, limit: int = 20, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    with _session(conn) as db:
        total = db.execute("SELECT COUNT(*) FROM version_groups").fetchone()[0]
        ids = [r[0] for r in db.execute("SELECT id FROM version_groups ORDER BY locked, confidence ASC, id LIMIT ? OFFSET ?", (limit, offset))]
        return {"total": total, "groups": [group_detail(i, db) for i in ids]}


def group_of(track_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[int]:
    with _session(conn) as db:
        r = db.execute("SELECT group_id FROM version_members WHERE track_id = ?", (track_id,)).fetchone()
        return r[0] if r else None


def set_primary(group_id: int, track_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
    with _session(conn) as db:
        if not db.execute("SELECT 1 FROM version_members WHERE group_id = ? AND track_id = ?", (group_id, track_id)).fetchone():
            raise ValueError("That track isn't in this group")
        db.execute("UPDATE version_groups SET primary_id = ?, locked = 1 WHERE id = ?", (track_id, group_id))
    smart_search.invalidate()


def ungroup(track_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
    """'This isn't a version of that.' Removes the track from its group and keeps it out of auto-grouping."""
    with _session(conn) as db:
        r = db.execute("SELECT group_id FROM version_members WHERE track_id = ?", (track_id,)).fetchone()
        db.execute("INSERT OR IGNORE INTO version_exclusions (track_id) VALUES (?)", (track_id,))
        if r:
            gid = r[0]
            db.execute("DELETE FROM version_members WHERE track_id = ?", (track_id,))
            left = [x[0] for x in db.execute("SELECT track_id FROM version_members WHERE group_id = ?", (gid,))]
            if len(left) < 2:
                db.execute("DELETE FROM version_groups WHERE id = ?", (gid,))
            else:
                p = db.execute("SELECT primary_id FROM version_groups WHERE id = ?", (gid,)).fetchone()[0]
                if p == track_id:
                    tr = {t["id"]: dict(t) for t in db.execute(f"SELECT id, filename, duration_sec, file_size, file_mtime, format FROM tracks WHERE id IN ({','.join('?' * len(left))})", left)}
                    db.execute("UPDATE version_groups SET primary_id = ? WHERE id = ?", (max(left, key=lambda i: primary_score(tr[i])), gid))
                db.execute("UPDATE version_groups SET locked = 1 WHERE id = ?", (gid,))
    smart_search.invalidate()


def merge(track_ids: Iterable[int], conn: Optional[sqlite3.Connection] = None) -> int:
    """'These are the same track.' Builds one locked group from the given tracks (absorbing any groups they were in)."""
    ids = list(dict.fromkeys(int(i) for i in track_ids))
    if len(ids) < 2:
        raise ValueError("Pick at least two tracks to group")
    with _session(conn) as db:
        q = ",".join("?" * len(ids))
        tracks = {t["id"]: dict(t) for t in db.execute(f"SELECT id, filename, duration_sec, file_size, file_mtime, format FROM tracks WHERE id IN ({q})", ids)}
        if len(tracks) < 2:
            raise ValueError("Those tracks don't exist")
        every = set(tracks)
        for r in db.execute(f"SELECT DISTINCT group_id FROM version_members WHERE track_id IN ({q})", ids).fetchall():
            every |= {x[0] for x in db.execute("SELECT track_id FROM version_members WHERE group_id = ?", (r[0],))}
            db.execute("DELETE FROM version_groups WHERE id = ?", (r[0],))
        missing = every - set(tracks)
        if missing:
            tracks.update({t["id"]: dict(t) for t in db.execute(f"SELECT id, filename, duration_sec, file_size, file_mtime, format FROM tracks WHERE id IN ({','.join('?' * len(missing))})", list(missing))})
        for i in every:
            db.execute("DELETE FROM version_exclusions WHERE track_id = ?", (i,))
        primary = max(every, key=lambda i: primary_score(tracks[i]))
        gid = db.execute("INSERT INTO version_groups (primary_id, kind, confidence, locked, created_at) VALUES (?,?,1.0,1,?)",
                         (primary, "version", _now())).lastrowid
        db.executemany("INSERT INTO version_members (track_id, group_id) VALUES (?,?)", [(i, gid) for i in every])
    smart_search.invalidate()
    return gid
