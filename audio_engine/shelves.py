# audio_engine/shelves.py
"""The Discover home: shelves built from your own library (nothing generic). Each shelf says what it is and how to open it in full."""
import datetime
import random
from collections import Counter
from typing import Any, Dict, List, Optional

import numpy as np

from . import features as F, genres as G, similarity as sim, smart_search as ss
from .library import _session
from .tagging import tag_kind


def _tracks(db, items, n):
    return [ss._row((0.0, False, [], [], t), db, []) for t in items[:n]]


def favorites_ids(db) -> List[int]:
    """Tracks you've shown you like: rated 4-5 stars or hearted."""
    ids = {r[0] for r in db.execute("SELECT track_id FROM track_user WHERE rating >= 4")}
    ids |= {r[0] for r in db.execute("SELECT ct.track_id FROM collection_tracks ct JOIN collections c ON c.id = ct.collection_id WHERE c.name = '♥ Liked'")}
    return sorted(ids)


def similar_to_set(ids: List[int], per_page: int = 40, conn=None) -> Dict[str, Any]:
    """Tracks that sound like the AVERAGE of several tracks (your favorites), excluding those tracks and non-primary versions."""
    with _session(conn) as db:
        idx = ss._index(db)
        c = sim._load(db)
        have = [i for i in ids if i in c["index"]]
        if not have:
            res = ss.search("", per_page=per_page, conn=db)
            res["notes"] = ["None of those tracks are analyzed for sound yet, so here are some picks."] + res["notes"]
            return res
        centroid = c["Z"][[c["index"][i] for i in have]].mean(0, keepdims=True)
        q = np.zeros_like(centroid)
        for g, sl in F.SLICES.items():                       # re-normalise each group after averaging
            v = centroid[:, sl]; q[:, sl] = v / max(1e-9, float(np.linalg.norm(v)))
        score, per = sim._scores(q[0], c["Z"], sim.DEFAULT_WEIGHTS)
        hits = []
        for i in np.argsort(-score):
            tid = int(c["ids"][i]); t = idx["by_id"].get(tid)
            if tid in ids or t is None or (t.get("group") and not t["group"]["is_primary"]):
                continue
            hits.append({"id": tid, "score": float(score[i]), "groups": {g: float(per[g][i]) for g in per}})
            if len(hits) >= 300:
                break
        return ss._audio_response(db, idx, hits, sim.DEFAULT_WEIGHTS, [{"kind": "similar", "label": f"your {len(have)} favorites"}],
                                  [f"Tracks that sound like the average of your {len(have)} favorites (4–5★ and hearted)."], 1, per_page)


def build(seed: Optional[int] = None, conn=None) -> Dict[str, Any]:
    rnd = random.Random(seed if seed is not None else datetime.date.today().toordinal())
    with _session(conn) as db:
        idx = ss._index(db)
        pool = [t for t in idx["tracks"] if not t.get("group") or t["group"]["is_primary"]]       # one entry per stack
        shelves: List[Dict[str, Any]] = []
        if not pool:
            return {"shelves": [], "empty": True}

        # genres + moods as browse tiles
        tagc = Counter(g for t in pool for g in t["tags"])
        for kind, title, sub in (("genre", "Browse by genre", "Your library, sorted by sound of the music"),
                                 ("mood", "Browse by mood", "Tagged from how the audio feels")):
            tiles = [{"tag": g, "count": n} for g, n in tagc.most_common() if tag_kind(g) == kind][:14]
            if tiles:
                shelves.append({"id": kind + "s", "title": title, "subtitle": sub, "type": "tiles", "items": tiles})
        ov = G.overview(db)
        if ov["unlabeled"] and ov["genres"]:
            shelves.append({"id": "needs-genre", "title": "Needs a genre", "type": "cta", "items": [],
                            "subtitle": f"{ov['unlabeled']:,} of {ov['tracks']:,} tracks have no genre yet", "action": {"open": "genres"},
                            "button": "Review genre suggestions"})

        never = [t for t in pool if t.get("sent_count", 0) == 0]
        never.sort(key=lambda t: (-(t.get("rating") or 0), "beat" not in t["tags"], t.get("created_at") or ""), reverse=False)
        if never:
            shelves.append({"id": "never-sent", "title": "Never pitched", "type": "tracks",
                            "subtitle": f"{len(never):,} tracks you haven't sent to anyone", "items": _tracks(db, never, 8),
                            "query": {"sent": "never", "sort": "newest"}})

        favs = favorites_ids(db)
        if len(favs) >= 2:
            c = sim._load(db)
            if sum(1 for i in favs if i in c["index"]) >= 2:
                shelves.append({"id": "like-favs", "title": "Sounds like your favorites", "type": "tracks",
                                "subtitle": f"Built from your {len(favs)} starred / hearted tracks",
                                "items": similar_to_set(favs, 8, db)["tracks"], "action": {"similar_set": favs}})

        yr = datetime.date.today().year
        old = [t for t in pool if any(g.isdigit() and int(g) <= yr - 2 for g in t["tags"] if len(g) == 4) and not t.get("rating")]
        if old:
            rnd.shuffle(old)
            year = next((g for g in old[0]["tags"] if g.isdigit()), None)
            shelves.append({"id": "remember", "title": "Remember these?", "type": "tracks",
                            "subtitle": f"Unrated, from {yr - 2} or earlier" , "items": _tracks(db, old, 8), "query": {"sort": "random"}})

        newest = sorted(pool, key=lambda t: t.get("created_at") or "", reverse=True)
        shelves.append({"id": "recent", "title": "Recently added", "type": "tracks", "subtitle": "Newest in your library",
                        "items": _tracks(db, newest, 8), "query": {"sort": "newest"}})

        n_groups = db.execute("SELECT COUNT(*) FROM version_groups").fetchone()[0]
        if n_groups:
            shelves.append({"id": "versions", "title": "Versions stacked", "type": "cta", "items": [],
                            "subtitle": f"{n_groups:,} groups of the same track are stacked into one row each",
                            "action": {"open": "versions"}, "button": "Review stacks"})
        return {"shelves": shelves}
