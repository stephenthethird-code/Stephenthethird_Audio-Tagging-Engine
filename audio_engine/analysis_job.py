# audio_engine/analysis_job.py
"""
Background, resumable audio analysis: fingerprint (features.py) + key + tempo for every track.

  * Resumable: "pending" = tracks with no up-to-date row in track_features, recomputed from the DB on every start.
    Stop the server mid-run and just press Start again; finished tracks are never redone.
  * Files that can't be read are recorded with an error (so they aren't retried forever); files whose drive is
    unplugged are only counted as "missing" and stay pending.
  * Detected values go to the *detected* layer (`tracks`). A tempo written in the file name still wins over the
    audio estimate (producers know their BPM; beat trackers confuse half/double time); your edits win over both.
"""
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from .db import connect, init_db
from .library import _now
from . import features, smart_search
from .similarity import invalidate as _invalidate_vectors

BATCH_COMMIT = 10


class AnalysisJob:
    def __init__(self, total: int):
        self.state = "running"          # running | done | cancelled | error
        self.total, self.done, self.failed, self.missing = total, 0, 0, 0
        self.current: Optional[str] = None
        self.error: Optional[str] = None
        self.started = time.time(); self.finished: Optional[float] = None
        self._cancel = threading.Event()

    def public(self) -> Dict[str, Any]:
        elapsed = (self.finished or time.time()) - self.started
        rate = self.done / elapsed if elapsed > 0 and self.done else 0
        eta = int((self.total - self.done - self.failed - self.missing) / rate) if rate and self.state == "running" else None
        return {"state": self.state, "total": self.total, "done": self.done, "failed": self.failed, "missing": self.missing,
                "current": self.current, "error": self.error, "elapsed": round(elapsed, 1), "eta_seconds": eta}


_JOB: Optional[AnalysisJob] = None
_LOCK = threading.Lock()


def pending_ids(db: sqlite3.Connection, only_ids: Optional[Iterable[int]] = None) -> List[int]:
    rows = db.execute("SELECT t.id FROM tracks t LEFT JOIN track_features f ON f.track_id = t.id AND f.version = ? "
                      "WHERE f.track_id IS NULL ORDER BY t.id", (features.VERSION,)).fetchall()
    ids = [r[0] for r in rows]
    if only_ids is not None:
        keep = set(int(i) for i in only_ids)
        ids = [i for i in ids if i in keep]
    return ids


def coverage(db: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    own = db is None
    db = db or connect()
    try:
        total = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        ok = db.execute("SELECT COUNT(*) FROM track_features WHERE version = ? AND vec IS NOT NULL", (features.VERSION,)).fetchone()[0]
        bad = db.execute("SELECT COUNT(*) FROM track_features WHERE version = ? AND vec IS NULL", (features.VERSION,)).fetchone()[0]
        return {"total": total, "analyzed": ok, "failed": bad, "pending": total - ok - bad}
    finally:
        if own:
            db.close()


def status() -> Dict[str, Any]:
    return {"job": _JOB.public() if _JOB else None, **coverage()}


def start(only_ids: Optional[Iterable[int]] = None, limit: Optional[int] = None) -> AnalysisJob:
    """Start (or return the running) analysis. only_ids restricts it to e.g. the tracks you just selected/imported."""
    global _JOB
    init_db()
    with _LOCK:
        if _JOB and _JOB.state == "running":
            return _JOB
        db = connect()
        try:
            ids = pending_ids(db, only_ids)
        finally:
            db.close()
        if limit:
            ids = ids[:int(limit)]
        _JOB = AnalysisJob(len(ids))
        job = _JOB
    threading.Thread(target=_run, args=(job, ids), daemon=True).start()
    return job


def cancel() -> bool:
    if _JOB and _JOB.state == "running":
        _JOB._cancel.set()
        return True
    return False


def _store(db: sqlite3.Connection, track_id: int, res: Dict[str, Any]) -> None:
    db.execute("INSERT OR REPLACE INTO track_features (track_id, version, vec, analysis_bpm, analysis_key, key_conf, seconds, error, analyzed_at) "
               "VALUES (?,?,?,?,?,?,?,NULL,?)",
               (track_id, features.VERSION, res["vec"].tobytes(), res["bpm"], res["key"], res["key_conf"], res["seconds"], _now()))
    row = db.execute("SELECT bpm, camelot_key FROM tracks WHERE id = ?", (track_id,)).fetchone()
    sets, vals = ["analyzed_at = ?"], [_now()]
    if res["key"] and not row["camelot_key"]:
        sets += ["camelot_key = ?", "key = COALESCE(key, ?)"]; vals += [res["key"], res["key"]]
    if res["bpm"] and row["bpm"] is None:
        sets += ["bpm = ?", "bpm_source = 'analysis'"]; vals.append(res["bpm"])
    db.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE id = ?", vals + [track_id])


def _run(job: AnalysisJob, ids: List[int]) -> None:
    db = connect()
    try:
        for n, tid in enumerate(ids, 1):
            if job._cancel.is_set():
                job.state = "cancelled"; break
            row = db.execute("SELECT path FROM tracks WHERE id = ?", (tid,)).fetchone()
            job.current = os.path.basename(row["path"]) if row else None
            if not row or not os.path.isfile(row["path"]):
                job.missing += 1                                  # drive unplugged / file moved: stays pending, not an error
                continue
            try:
                _store(db, tid, features.analyze_path(row["path"]))
                job.done += 1
            except Exception as exc:
                db.execute("INSERT OR REPLACE INTO track_features (track_id, version, vec, error, analyzed_at) VALUES (?,?,NULL,?,?)",
                           (tid, features.VERSION, str(exc)[:300], _now()))
                job.failed += 1
            if n % BATCH_COMMIT == 0:
                db.commit()
        db.commit()
        if job.state == "running":
            job.current = "organizing…"
            _derive()                                   # moods + version stacks, BEFORE reporting done (readers see a finished library)
            job.state = "done"
    except Exception as exc:
        job.state, job.error = "error", str(exc)
    finally:
        db.commit(); db.close()
        job.finished = time.time(); job.current = None
        smart_search.invalidate(); _invalidate_vectors()


def _derive() -> None:
    """Things computed from the fingerprints. A convenience: a failure here must never fail the analysis itself."""
    try:
        from . import moods, versions
        _invalidate_vectors()
        moods.apply(); versions.rebuild()
    except Exception:
        pass
