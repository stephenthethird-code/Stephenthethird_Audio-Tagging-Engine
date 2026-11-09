# audio_engine/moods.py
"""
Mood tags from the audio. HEURISTIC, not a trained model: every rule is a plain combination of measured features,
judged *relative to your own library* (percentile ranks), so "bright" means brighter than most of YOUR tracks.

    dark       minor key  + duller than most                  bright      brighter than most (+ major key)
    chill      sparse onsets + quiet (+ slow)                   hard        loud + dense onsets
    energetic  dense onsets + loud + fast (130+ bpm)            uplifting   major key + bright + not slow

Moods are added as ordinary tags, marked "auto" (✦). Remove one and it is rejected for good - it is never re-added.
"""
import sqlite3
from typing import Any, Dict, List, Optional

import numpy as np

from . import features as F, smart_search
from .library import _session

MIN_TRACKS = 20           # percentiles mean nothing on a handful of tracks
THRESHOLDS = {"dark": .70, "bright": .80, "chill": .70, "hard": .80, "energetic": .75, "uplifting": .75}
MAX_PER_TRACK = 2


def _ranks(x: np.ndarray) -> np.ndarray:
    return np.argsort(np.argsort(x)) / max(1, len(x) - 1)


def compute(db: sqlite3.Connection) -> Dict[int, List[tuple]]:
    rows = db.execute("SELECT f.track_id, f.vec, f.analysis_bpm, f.key_conf, t.bpm, t.camelot_key FROM track_features f "
                      "JOIN tracks t ON t.id = f.track_id WHERE f.vec IS NOT NULL AND f.version = ?", (F.VERSION,)).fetchall()
    if len(rows) < MIN_TRACKS:
        return {}
    X = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    bright = _ranks(X[:, F.SLICES["spectrum"].start])            # log spectral centroid
    onset = _ranks(X[:, F.SLICES["energy"].start + 3])
    loud = _ranks(X[:, F.SLICES["energy"].start])
    out: Dict[int, List[tuple]] = {}
    for i, r in enumerate(rows):
        key = r["camelot_key"]
        mode = None if not key or (r["key_conf"] or 0) < 0.4 else (1.0 if key.endswith("B") else 0.0)   # 1 major, 0 minor
        minor = 0.5 if mode is None else 1.0 - mode
        major = 0.5 if mode is None else mode
        tempo = r["bpm"] or r["analysis_bpm"] or 0
        scores = {
            "dark": .5 * minor + .5 * (1 - bright[i]),
            "bright": .7 * bright[i] + .3 * major,
            "chill": .5 * (1 - onset[i]) + .3 * (1 - loud[i]) + .2 * (1.0 if 0 < tempo <= 105 else 0.0),
            "hard": .5 * loud[i] + .5 * onset[i],
            "energetic": .4 * onset[i] + .3 * loud[i] + .3 * (1.0 if tempo >= 130 else 0.0),
            "uplifting": .4 * major + .3 * bright[i] + .3 * (1.0 if tempo >= 105 else 0.0),
        }
        top = sorted(((m, round(s, 3)) for m, s in scores.items() if s >= THRESHOLDS[m]), key=lambda x: -x[1])[:MAX_PER_TRACK]
        out[int(r["track_id"])] = top
    return out


def apply(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """(Re)derive mood tags. Replaces earlier auto moods that no longer fit; never touches tags you typed or rejected."""
    with _session(conn) as db:
        derived = compute(db)
        if not derived:
            n = db.execute("SELECT COUNT(*) FROM track_features WHERE vec IS NOT NULL").fetchone()[0]
            return {"applied": False, "reason": f"Moods need at least {MIN_TRACKS} analyzed tracks (you have {n}).", "added": 0, "removed": 0}
        added = removed = 0
        for tid, moods in derived.items():
            want = {m for m, _ in moods}
            have = {r["tag"]: r["rejected"] for r in db.execute("SELECT tag, rejected FROM auto_tags WHERE track_id = ? AND source = 'audio-mood'", (tid,))}
            for tag, rejected in have.items():
                if tag not in want and not rejected:                  # no longer fits and the user never touched it
                    db.execute("DELETE FROM track_tags WHERE track_id = ? AND tag = ?", (tid, tag))
                    db.execute("DELETE FROM auto_tags WHERE track_id = ? AND tag = ?", (tid, tag)); removed += 1
            for tag, conf in moods:
                if tag in have and have[tag]:
                    continue                                          # user rejected it before
                typed = db.execute("SELECT 1 FROM track_tags WHERE track_id = ? AND tag = ?", (tid, tag)).fetchone() and tag not in have
                db.execute("INSERT OR IGNORE INTO track_tags (track_id, tag) VALUES (?, ?)", (tid, tag))
                if not typed:                                         # a tag the user already typed stays theirs, not 'auto'
                    cur = db.execute("INSERT OR IGNORE INTO auto_tags (track_id, tag, source, confidence) VALUES (?,?, 'audio-mood', ?)", (tid, tag, conf))
                    added += cur.rowcount
    smart_search.invalidate()
    return {"applied": True, "added": added, "removed": removed, "tracks": len(derived)}
