# audio_engine/similarity.py
"""
Sound-based nearest neighbours over the track_features fingerprints.

Each feature group is z-scored against the whole library, L2-normalised, and compared by cosine; the final score is
the weighted mean over the groups the user left switched on. Pure numpy: ~2k tracks answer in a few milliseconds.
"""
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import features as F
from .library import _session

DEFAULT_WEIGHTS = {"timbre": 1.0, "spectrum": 1.0, "pitch": 0.6, "energy": 0.6, "rhythm": 0.8}
_C: Dict[str, Any] = {"sig": None}


def invalidate() -> None:
    _C["sig"] = None


def _load(db: sqlite3.Connection) -> Dict[str, Any]:
    sig = tuple(db.execute("SELECT COUNT(*), COALESCE(MAX(analyzed_at),'') FROM track_features WHERE vec IS NOT NULL AND version = ?",
                           (F.VERSION,)).fetchone())
    path = next((r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"), "")
    if _C["sig"] == (path, sig):
        return _C
    rows = db.execute("SELECT track_id, vec, analysis_bpm, analysis_key FROM track_features WHERE vec IS NOT NULL AND version = ?",
                      (F.VERSION,)).fetchall()
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    X = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows]) if rows else np.zeros((0, F.DIM), np.float32)
    mean = X.mean(0) if len(X) else np.zeros(F.DIM, np.float32)
    std = X.std(0) if len(X) else np.ones(F.DIM, np.float32)
    std = np.where(std < 1e-6, 1.0, std)
    _C.update(sig=(path, sig), ids=ids, index={int(i): k for k, i in enumerate(ids)}, mean=mean, std=std,
              Z=_normalise(X, mean, std), meta={int(r[0]): (r[2], r[3]) for r in rows})
    return _C


def _normalise(X: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """z-score then L2-normalise each group, so a group's weight is its *similarity*, not its dimensionality."""
    Z = (np.atleast_2d(X) - mean) / std
    out = np.zeros_like(Z, dtype=np.float32)
    for name, sl in F.SLICES.items():
        g = Z[:, sl]
        n = np.linalg.norm(g, axis=1, keepdims=True)
        out[:, sl] = g / np.where(n < 1e-9, 1.0, n)
    return out


def parse_weights(spec: Optional[Sequence[str]]) -> Dict[str, float]:
    """['timbre','pitch'] -> only those groups on. None/empty -> defaults."""
    if not spec:
        return dict(DEFAULT_WEIGHTS)
    on = {g: DEFAULT_WEIGHTS[g] for g in spec if g in DEFAULT_WEIGHTS}
    return on or dict(DEFAULT_WEIGHTS)


def _scores(q: np.ndarray, Z: np.ndarray, weights: Dict[str, float]) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    total = sum(weights.values())
    with np.errstate(all="ignore"):       # numpy 2 + macOS Accelerate emits spurious BLAS warnings; verified results are exact to ~3e-7
        per = {g: Z[:, F.SLICES[g]] @ q[F.SLICES[g]] for g in weights}
    return sum(weights[g] * per[g] for g in weights) / total, per


def has_vector(db: sqlite3.Connection, track_id: int) -> bool:
    return track_id in _load(db)["index"]


def neighbours(db: sqlite3.Connection, query_vec: Optional[np.ndarray] = None, track_id: Optional[int] = None,
               weights: Optional[Dict[str, float]] = None, k: int = 200) -> List[Dict[str, Any]]:
    """Closest analysed tracks. Give either a track_id (it is excluded from its own results) or a raw fingerprint."""
    c = _load(db)
    if not len(c["ids"]):
        return []
    weights = weights or dict(DEFAULT_WEIGHTS)
    if track_id is not None:
        if track_id not in c["index"]:
            return []
        q = c["Z"][c["index"][track_id]]
    else:
        q = _normalise(np.asarray(query_vec, dtype=np.float32), c["mean"], c["std"])[0]
    score, per = _scores(q, c["Z"], weights)
    order = np.argsort(-score)
    out = []
    for i in order:
        tid = int(c["ids"][i])
        if tid == track_id:
            continue
        out.append({"id": tid, "score": float(score[i]), "groups": {g: float(per[g][i]) for g in weights}})
        if len(out) >= k:
            break
    return out


def pct(score: float) -> int:
    """cosine (-1..1) -> 0..100; unrelated tracks land near 0-20, near-duplicates near 90+."""
    return int(round(max(0.0, min(1.0, score)) * 100))


def why(groups: Dict[str, float]) -> str:
    """'timbre 92% · pitch 71%' - the strongest two reasons a track matched."""
    top = sorted(groups.items(), key=lambda kv: -kv[1])[:2]
    return " · ".join(f"{g} {pct(v)}%" for g, v in top)
