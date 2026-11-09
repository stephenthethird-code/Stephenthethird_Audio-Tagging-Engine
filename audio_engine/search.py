# audio_engine/search.py
"""
Faceted tag search + collections + auto-tag pass over the library DB (the strict, filter-style layer; the app's
main search is smart_search.py, which never returns an empty result).

One filter {text, tags, tags_exclude, key, bpm | bpm_min..bpm_max, sample_type, collection, sort} produces (a) a page of
results and (b) a tag_summary = tag -> count over ALL matches (not just the page), with selected tags listed first even at count 0.
"""
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from .library import _normalize_tags, _now, _session, normalize_tag
from .tagging import suggest_tags

SORTS = {"name": "LOWER(t.filename)", "bpm": "t.bpm", "key": "t.camelot_key", "duration": "t.duration_sec",
         "random": "RANDOM()", "newest": "t.created_at"}  # whitelist: never interpolate user input


def _filter_sql(text=None, tags=None, tags_exclude=None, key=None, bpm=None, bpm_min=None, bpm_max=None,
                sample_type=None, collection=None):
    """Build 'FROM tracks t WHERE ...' + params. Tags AND together (each selected tag narrows the set)."""
    where, params = ["1=1"], []
    for token in [x for x in re.split(r"[ _\-]+", text or "") if x]:  # split on spaces, underscores and dashes
        where.append("(LOWER(t.filename) LIKE ? OR LOWER(t.path) LIKE ?)")
        params += [f"%{token.lower()}%"] * 2
    for tag in _normalize_tags(tags):
        where.append("EXISTS (SELECT 1 FROM track_tags g WHERE g.track_id = t.id AND g.tag = ?)")
        params.append(tag)
    for tag in _normalize_tags(tags_exclude):
        where.append("NOT EXISTS (SELECT 1 FROM track_tags g WHERE g.track_id = t.id AND g.tag = ?)")
        params.append(tag)
    if key:
        where.append("(LOWER(t.key) = ? OR LOWER(t.camelot_key) = ?)")
        params += [key.lower()] * 2
    if bpm is not None:
        where.append("t.bpm BETWEEN ? AND ?")
        params += [bpm - 0.5, bpm + 0.5]
    if bpm_min is not None:
        where.append("t.bpm >= ?"); params.append(bpm_min)
    if bpm_max is not None:
        where.append("t.bpm <= ?"); params.append(bpm_max)
    if sample_type:
        where.append("t.sample_type = ?"); params.append(sample_type)
    if collection:
        where.append("t.id IN (SELECT ct.track_id FROM collection_tracks ct JOIN collections c ON c.id = ct.collection_id "
                     "WHERE c.name = ?)")
        params.append(collection)
    return "FROM tracks t WHERE " + " AND ".join(where), params


def search_tracks(text: Optional[str] = None, tags: Optional[Iterable[str]] = None,
                  tags_exclude: Optional[Iterable[str]] = None, key: Optional[str] = None,
                  bpm: Optional[float] = None, bpm_min: Optional[float] = None, bpm_max: Optional[float] = None,
                  sample_type: Optional[str] = None, collection: Optional[str] = None,
                  sort: str = "name", descending: bool = False, page: int = 1, per_page: int = 50,
                  conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Returns {total, tracks: [...with 'tags'], tag_summary: [{tag, count}], page, per_page}."""
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {sorted(SORTS)}")
    from_sql, params = _filter_sql(text, tags, tags_exclude, key, bpm, bpm_min, bpm_max, sample_type, collection)
    order = f"{SORTS[sort]} {'DESC' if descending else 'ASC'}, t.id"
    per_page = max(1, min(int(per_page), 500))
    offset = (max(1, int(page)) - 1) * per_page

    with _session(conn) as db:
        total = db.execute(f"SELECT COUNT(*) {from_sql}", params).fetchone()[0]
        rows = [dict(r) for r in db.execute(
            f"SELECT t.* {from_sql} ORDER BY {order} LIMIT ? OFFSET ?", params + [per_page, offset])]
        ids = [r["id"] for r in rows]
        by_track: Dict[int, List[str]] = {i: [] for i in ids}
        if ids:
            q = ",".join("?" * len(ids))
            for r in db.execute(f"SELECT track_id, tag FROM track_tags WHERE track_id IN ({q}) ORDER BY tag", ids):
                by_track[r["track_id"]].append(r["tag"])
        for r in rows:
            r["tags"] = by_track[r["id"]]

        # tag_summary over the WHOLE filtered set, not just this page
        counts = {r["tag"]: r["n"] for r in db.execute(
            f"SELECT g.tag AS tag, COUNT(*) AS n FROM track_tags g WHERE g.track_id IN (SELECT t.id {from_sql}) "
            f"GROUP BY g.tag ORDER BY n DESC, g.tag", params)}

    summary = [{"tag": t, "count": 0} for t in _normalize_tags(tags_exclude)]          # excluded first, count 0
    summary += [{"tag": t, "count": counts.get(t, 0)} for t in _normalize_tags(tags) if t not in counts]
    summary += [{"tag": t, "count": n} for t, n in counts.items()]
    return {"total": total, "tracks": rows, "tag_summary": summary, "page": page, "per_page": per_page}


# ---------- tag editing ----------

def untag_track(track_id: int, tags: Iterable[str], conn: Optional[sqlite3.Connection] = None) -> None:
    with _session(conn) as db:
        for t in _normalize_tags(tags):
            db.execute("DELETE FROM track_tags WHERE track_id = ? AND tag = ?", (track_id, t))


def rename_tag(old: str, new: str, conn: Optional[sqlite3.Connection] = None) -> int:
    """Rename/merge a tag across the library. Returns number of tracks touched."""
    old, new = normalize_tag(old), normalize_tag(new)
    if not old or not new:
        raise ValueError("both tag names are required")
    with _session(conn) as db:
        n = db.execute("SELECT COUNT(*) FROM track_tags WHERE tag = ?", (old,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO track_tags (track_id, tag) SELECT track_id, ? FROM track_tags WHERE tag = ?",
                   (new, old))
        db.execute("DELETE FROM track_tags WHERE tag = ?", (old,))
        return n


def list_tags(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    with _session(conn) as db:
        return [dict(r) for r in db.execute(
            "SELECT tag, COUNT(*) AS count FROM track_tags GROUP BY tag ORDER BY count DESC, tag")]


# ---------- auto-tag pass ----------

def autotag_library(dry_run: bool = False, conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """
    Fill tags / sample_type / (missing) BPM from file names + folders for every track.
    Never overwrites a BPM that came from real audio analysis; filename BPMs are marked bpm_source='filename'.
    Idempotent: safe to re-run.
    """
    stats = {"tracks": 0, "tags_added": 0, "bpm_filled": 0, "sample_type_set": 0}
    with _session(conn) as db:
        for row in db.execute("SELECT id, path, duration_sec, bpm, bpm_source, sample_type FROM tracks").fetchall():
            stats["tracks"] += 1
            s = suggest_tags(row["path"], row["duration_sec"])
            have = {r["tag"] for r in db.execute("SELECT tag FROM track_tags WHERE track_id = ?", (row["id"],))}
            new = [t for t in s["tags"] if t not in have]
            stats["tags_added"] += len(new)
            if row["sample_type"] != s["sample_type"] and s["sample_type"]:
                stats["sample_type_set"] += 1
            fill_bpm = s["bpm"] is not None and (row["bpm"] is None or row["bpm_source"] == "filename")
            if fill_bpm and row["bpm"] != s["bpm"]:
                stats["bpm_filled"] += 1
            if dry_run:
                continue
            for t in new:
                db.execute("INSERT OR IGNORE INTO track_tags (track_id, tag) VALUES (?, ?)", (row["id"], t))
            sets, vals = ["sample_type = COALESCE(?, sample_type)"], [s["sample_type"]]
            if fill_bpm:
                sets += ["bpm = ?", "bpm_source = 'filename'"]
                vals.append(s["bpm"])
            db.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE id = ?", vals + [row["id"]])
    return stats


# ---------- collections ----------

def create_collection(name: str, description: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("collection name is required")
    with _session(conn) as db:
        row = db.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        now = _now()
        return db.execute("INSERT INTO collections (name, description, created_at, updated_at) VALUES (?,?,?,?)",
                          (name, description, now, now)).lastrowid


def add_to_collection(name: str, track_ids: Iterable[int], conn: Optional[sqlite3.Connection] = None) -> int:
    """Add tracks to a collection (created if missing). Returns how many were newly added."""
    added = 0
    with _session(conn) as db:
        cid = create_collection(name, conn=db)
        for tid in track_ids:
            cur = db.execute("INSERT OR IGNORE INTO collection_tracks (collection_id, track_id, added_at) "
                             "SELECT ?, id, ? FROM tracks WHERE id = ?", (cid, _now(), tid))
            added += cur.rowcount
        return added


def remove_from_collection(name: str, track_ids: Iterable[int], conn: Optional[sqlite3.Connection] = None) -> None:
    with _session(conn) as db:
        for tid in track_ids:
            db.execute("DELETE FROM collection_tracks WHERE track_id = ? AND collection_id = "
                       "(SELECT id FROM collections WHERE name = ?)", (tid, name))


def list_collections(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    with _session(conn) as db:
        return [dict(r) for r in db.execute(
            """SELECT c.id, c.name, c.description, COUNT(ct.track_id) AS track_count
               FROM collections c LEFT JOIN collection_tracks ct ON ct.collection_id = c.id
               GROUP BY c.id ORDER BY c.name""")]
