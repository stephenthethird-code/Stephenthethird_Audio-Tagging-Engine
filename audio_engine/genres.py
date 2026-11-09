# audio_engine/genres.py
"""
Genres as a first-class dimension of the library.

Genre tags come from file names / folders / file metadata (tagging.py) and from you. For everything still unlabeled,
this module SUGGESTS a genre by looking at the tracks you have already labeled: if a track sounds like your trap tracks
it suggests "trap". It learns from YOUR examples, so it can only suggest genres you have labeled at least MIN_EXAMPLES
tracks for - and it only suggests, never tags on its own. Accept / decline each, or accept in bulk by confidence.
"""
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from . import features as F, similarity as sim, smart_search
from .library import normalize_tag, _session
from .tagging import GENRES, tag_kind

MIN_EXAMPLES = 3
NEIGHBOURS = 7
WEIGHTS = {"timbre": 1.0, "spectrum": 1.0, "energy": 0.6, "rhythm": 0.8}      # pitch left out: genre isn't a key


def known_genres(db: sqlite3.Connection) -> Dict[str, int]:
    """genre tag -> number of tracks carrying it (vocabulary genres plus any tag already used that way)."""
    vocab = {normalize_tag(g) for g in GENRES}
    counts = {r["tag"]: r["n"] for r in db.execute("SELECT tag, COUNT(*) n FROM track_tags GROUP BY tag")}
    return {t: n for t, n in counts.items() if t in vocab or tag_kind(t) == "genre"}


def overview(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    with _session(conn) as db:
        total = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        genres = known_genres(db)
        labeled = db.execute("SELECT COUNT(DISTINCT track_id) FROM track_tags WHERE tag IN (%s)" % ",".join("?" * len(genres)),
                             list(genres)).fetchone()[0] if genres else 0
        analyzed = db.execute("SELECT COUNT(*) FROM track_features WHERE vec IS NOT NULL").fetchone()[0]
        rows = [{"genre": g, "count": n, "can_suggest": n >= MIN_EXAMPLES, "examples_needed": max(0, MIN_EXAMPLES - n)}
                for g, n in sorted(genres.items(), key=lambda kv: -kv[1])]
        return {"genres": rows, "tracks": total, "labeled": labeled, "unlabeled": total - labeled, "analyzed": analyzed,
                "min_examples": MIN_EXAMPLES}


def suggestions(limit: int = 60, min_conf: float = 0.5, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    with _session(conn) as db:
        c = sim._load(db)
        genres = {g for g, n in known_genres(db).items() if n >= MIN_EXAMPLES}
        info: Dict[str, Any] = {"suggestions": [], "genres_used": sorted(genres)}
        if not len(c["ids"]):
            return {**info, "reason": "Analyze your library first - suggestions are based on how tracks sound."}
        tags: Dict[int, set] = {}
        for r in db.execute("SELECT track_id, tag FROM track_tags"):
            if r["tag"] in genres:
                tags.setdefault(r["track_id"], set()).add(r["tag"])
        index = c["index"]
        L = [t for t in tags if t in index]                              # labeled & analyzed
        if len(L) < MIN_EXAMPLES * 2 or len(genres) < 1:
            return {**info, "reason": f"Label a few tracks per genre first (at least {MIN_EXAMPLES} each) - the app learns from your examples."}
        rejected = {(r["track_id"], r["tag"]) for r in db.execute("SELECT track_id, tag FROM auto_tags WHERE source = 'genre-suggest' AND rejected = 1")}
        all_ids = [int(i) for i in c["ids"]]
        U = [t for t in all_ids if t not in tags]                        # analyzed, no genre yet
        if not U:
            return {**info, "reason": "Every analyzed track already has a genre."}
        names = {r["id"]: r["filename"] for r in db.execute("SELECT id, filename FROM tracks")}
        Z = c["Z"]; tot = sum(WEIGHTS.values())
        Lm = Z[[index[t] for t in L]]; Um = Z[[index[t] for t in U]]
        with np.errstate(all="ignore"):
            S = sum(w * (Um[:, F.SLICES[g]] @ Lm[:, F.SLICES[g]].T) for g, w in WEIGHTS.items()) / tot   # |U| x |L|
        out = []
        for ui, tid in enumerate(U):
            top = np.argsort(-S[ui])[:NEIGHBOURS]
            votes: Dict[str, float] = {}
            for li in top:
                w = max(float(S[ui, li]), 0.0) ** 2
                for g in tags[L[li]]:
                    votes[g] = votes.get(g, 0.0) + w
            total = sum(votes.values())
            if total <= 0:
                continue
            strength = float(np.clip(np.mean(S[ui, top]), 0, 1))
            for g, v in sorted(votes.items(), key=lambda kv: -kv[1])[:1]:
                conf = round((v / total) * min(1.0, 0.4 + strength), 3)
                if conf >= min_conf and (tid, g) not in rejected:
                    because = [names[L[li]] for li in top if g in tags[L[li]]][:3]
                    out.append({"track_id": tid, "filename": names[tid], "genre": g, "confidence": conf, "because": because})
        out.sort(key=lambda s: -s["confidence"])
        return {**info, "suggestions": out[:limit], "total": len(out),
                "reason": None if out else "No confident suggestions yet - label more examples of each genre."}


def accept(items: Iterable[Dict[str, Any]], conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """items: [{track_id, genre}]. Adds the genre as a tag (undoable, one batch per genre), marked auto."""
    from . import librarian
    by_genre: Dict[str, List[int]] = {}
    for it in items:
        by_genre.setdefault(normalize_tag(it["genre"]), []).append(int(it["track_id"]))
    batches = []
    with _session(conn) as db:
        for genre, ids in by_genre.items():
            r = librarian.apply_batch(ids, add_tags=[genre], label=f"accept genre “{genre}” for {len(ids)}", conn=db)
            batches.append(r["batch_id"])
            for tid in ids:
                db.execute("INSERT OR REPLACE INTO auto_tags (track_id, tag, source, confidence, rejected) VALUES (?,?, 'genre-suggest', NULL, 0)", (tid, genre))
    smart_search.invalidate()
    return {"accepted": sum(len(v) for v in by_genre.values()), "batch_ids": batches}


def reject(items: Iterable[Dict[str, Any]], conn: Optional[sqlite3.Connection] = None) -> int:
    n = 0
    with _session(conn) as db:
        for it in items:
            db.execute("INSERT OR REPLACE INTO auto_tags (track_id, tag, source, confidence, rejected) VALUES (?,?, 'genre-suggest', NULL, 1)",
                       (int(it["track_id"]), normalize_tag(it["genre"]))); n += 1
    return n
