# audio_engine/smart_search.py
"""
Never-empty library search.

Instead of filtering (which can return 0 rows), every track is SCORED against what the user asked for and
the best ones are returned. 'Exact' matches (every constraint satisfied) come first; if there are none the
user still gets the closest tracks plus an explanation and "try instead" queries that DO have results.

Facets (tag / bpm / key / sample-type, excluded tags, tag counts over the whole result set) are the usual sample-browser
conventions. Free-text parsing, typo correction, scoring, suggestions and 'more like this' are this project's own design.
"""
import difflib
import random
import re
import sqlite3
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .keys import compatibility, to_camelot
from .library import _normalize_tags, _session, normalize_tag
from .tagging import INSTRUMENTS, GENRES, MOODS, tag_kind
from . import similarity as sim

SYNONYMS = {"melancholy": "sad", "melancholic": "sad", "upbeat": "happy", "relaxed": "chill", "calm": "chill",
            "heavy": "hard", "808s": "808", "drum": "drums", "vocals": "vocal", "vox": "vocal", "beats": "beat",
            "samples": "sample", "loops": "loop", "records": "record", "songs": "record", "dreamlike": "dreamy"}
TYPE_WORDS = {"loop": "loop", "one shot": "one-shot", "oneshot": "one-shot", "one-shot": "one-shot", "full": "track"}
STOP = {"the", "a", "an", "and", "of", "for", "with", "by", "in", "to", "some", "me", "my", "like", "bpm", "key",
        "show", "find", "give", "something", "that", "sounds", "sound", "type", "x", "prod"}
WEIGHTS = {"tag": 3.0, "bpm": 3.0, "key": 2.0, "word": 3.0, "type": 2.0, "rating": 1.5, "sent": 2.5}
EXACT_BPM_SLACK = 3.0

# ---------------------------------------------------------------- index (in-memory, rebuilt when the DB changes)
_cache: Dict[str, Any] = {"sig": None}


def invalidate() -> None:
    """Drop the in-memory index (call after any write; the signature below is only a safety net for other processes)."""
    _cache["sig"] = None


def _tokens(s: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9']+", s.lower()) if t]


def _index(db: sqlite3.Connection) -> Dict[str, Any]:
    sig = (_db_path(db),
           tuple(db.execute("SELECT COUNT(*), COALESCE(MAX(updated_at),''), COALESCE(SUM(bpm),0) FROM tracks").fetchone()),
           tuple(db.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(tag)),0), COALESCE(MAX(rowid),0) FROM track_tags").fetchone()),
           tuple(db.execute("SELECT COUNT(*), COALESCE(MAX(updated_at),''), COALESCE(SUM(bpm),0) FROM track_user").fetchone()),
           tuple(db.execute("SELECT (SELECT COUNT(*) FROM version_members), (SELECT COALESCE(MAX(id),0) FROM version_groups), "
                            "(SELECT COUNT(*) FROM sends), (SELECT COUNT(*) FROM auto_tags WHERE rejected = 0)").fetchone()))
    if _cache["sig"] == sig:
        return _cache
    tags: Dict[int, set] = {}
    for r in db.execute("SELECT track_id, tag FROM track_tags"):
        tags.setdefault(r["track_id"], set()).add(r["tag"])
    mine = {r["track_id"]: dict(r) for r in db.execute("SELECT * FROM track_user")}
    groups = {r["track_id"]: dict(r) for r in db.execute(
        "SELECT m.track_id, m.group_id, g.primary_id, g.kind, g.confidence, (SELECT COUNT(*) FROM version_members x WHERE x.group_id = m.group_id) AS size "
        "FROM version_members m JOIN version_groups g ON g.id = m.group_id")}
    sent_direct = {r[0]: r[1] for r in db.execute("SELECT track_id, COUNT(*) FROM sends GROUP BY track_id")}
    sent_group: Dict[int, int] = {}
    for tid, n in sent_direct.items():                      # a send counts for the whole stack: you sent *a version* of it
        if tid in groups:
            sent_group[groups[tid]["group_id"]] = sent_group.get(groups[tid]["group_id"], 0) + n
    autos: Dict[int, set] = {}
    for r in db.execute("SELECT track_id, tag FROM auto_tags WHERE rejected = 0"):
        autos.setdefault(r["track_id"], set()).add(r["tag"])
    tracks = []
    for r in db.execute("SELECT * FROM tracks"):
        t = dict(r)
        t["tags"] = tags.get(t["id"], set())
        t["camelot"] = t.get("camelot_key") or to_camelot(t.get("key"))
        # user layer wins over detected values (non-destructive: `tracks` itself is never changed by edits)
        u = mine.get(t["id"], {})
        t["overridden"] = [f for f, col in (("bpm", "bpm"), ("key", "camelot_key"), ("sample_type", "sample_type")) if u.get(col) is not None]
        if u.get("bpm") is not None: t["bpm"], t["bpm_source"] = u["bpm"], "manual"
        if u.get("camelot_key"): t["camelot"] = u["camelot_key"]
        if u.get("sample_type"): t["sample_type"] = u["sample_type"]
        t["rating"], t["notes"] = u.get("rating"), u.get("notes")
        g = groups.get(t["id"])
        t["group"] = ({"id": g["group_id"], "size": g["size"], "kind": g["kind"], "is_primary": g["primary_id"] == t["id"],
                       "primary_id": g["primary_id"], "confidence": g["confidence"]} if g else None)
        t["group_id"] = g["group_id"] if g else None
        t["sent_count"] = sent_group.get(g["group_id"], 0) if g else sent_direct.get(t["id"], 0)
        t["auto_tags"] = autos.get(t["id"], set()) & t["tags"]
        t["tokens"] = set(_tokens(t["filename"])) | set(_tokens(t["path"]))
        t["name_tokens"] = set(_tokens(t["filename"]))
        t["haystack"] = (t["filename"] + " " + t["path"]).lower()
        tracks.append(t)
    tag_counts = Counter(g for ts in tags.values() for g in ts)
    words = Counter(w for t in tracks for w in t["name_tokens"] if len(w) >= 3 and not w.isdigit())
    _cache.update(sig=sig, tracks=tracks, by_id={t["id"]: t for t in tracks},
                  tag_counts=tag_counts, words=words)
    return _cache


def _db_path(db: sqlite3.Connection) -> str:
    return next((r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"), "")


# ---------------------------------------------------------------- query understanding
def _close(word: str, vocab: Iterable[str], cutoff=0.78) -> Optional[str]:
    m = difflib.get_close_matches(word, list(vocab), n=1, cutoff=cutoff)
    return m[0] if m else None


def parse_query(q: str, idx: Dict[str, Any]) -> Dict[str, Any]:
    """Free text -> {tags, words, bpm_min, bpm_max, key, sample_type, corrections, chips}. Never raises."""
    text = (q or "").lower().replace("–", "-").replace("—", "-")
    intent: Dict[str, Any] = {"tags": [], "words": [], "bpm_min": None, "bpm_max": None, "key": None,
                              "sample_type": None, "corrections": []}
    known_tags = set(idx["tag_counts"]) | {normalize_tag(w) for w in INSTRUMENTS | GENRES | MOODS}

    m = re.search(r"(?<!\d)(\d{2,3})\s*(?:-|to)\s*(\d{2,3})\s*(?:bpm)?(?!\d)", text)
    if m and 30 <= int(m.group(1)) < int(m.group(2)) <= 300:
        intent["bpm_min"], intent["bpm_max"] = float(m.group(1)), float(m.group(2))
        text = text.replace(m.group(0), " ")
    m = re.search(r"(?<!\d)(\d{2,3})\s*bpm\b", text) or re.search(r"\b(?:at|around|about|~)\s*(\d{2,3})\b", text)
    if m and intent["bpm_min"] is None:
        b = float(m.group(1)); intent["bpm_min"], intent["bpm_max"] = b - EXACT_BPM_SLACK, b + EXACT_BPM_SLACK
        text = text.replace(m.group(0), " ")
    m = re.search(r"\b(1[0-2]|[1-9])\s?([ab])\b", text)
    if m:
        intent["key"] = f"{int(m.group(1))}{m.group(2).upper()}"; text = text.replace(m.group(0), " ")
    else:
        m = re.search(r"\b([a-g])\s?(#|sharp|flat|b)?\s?(major|minor|maj|min)\b", text) or re.search(r"\b([a-g][#b]?)m\b", text)
        if m:
            intent["key"] = to_camelot(m.group(0)); text = text.replace(m.group(0), " ") if intent["key"] else text
    for y in re.findall(r"\b(19\d\d|20\d\d)\b", text):
        intent["tags"].append(y); text = text.replace(y, " ")
    if intent["bpm_min"] is None:  # a lone number in tempo range
        nums = [n for n in re.findall(r"(?<![\d.'#])(\d{2,3})(?![\d.'])", text) if 60 <= int(n) <= 200]
        if nums:
            b = float(nums[0]); intent["bpm_min"], intent["bpm_max"] = b - EXACT_BPM_SLACK, b + EXACT_BPM_SLACK
            text = re.sub(r"(?<![\d.'#])" + nums[0] + r"(?![\d.'])", " ", text, count=1)

    for phrase, st in TYPE_WORDS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            intent["sample_type"] = st; text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text)
    raw = [w for w in re.split(r"[^a-z0-9&']+", text) if w]
    i = 0
    while i < len(raw):
        w = raw[i]
        if w in STOP:
            i += 1; continue
        pair = normalize_tag(w + " " + raw[i + 1]) if i + 1 < len(raw) else None
        if pair and pair in known_tags:                      # 'hip hop', 'one shot' style two-word tags
            intent["tags"].append(pair); i += 2; continue
        w = SYNONYMS.get(w, w)
        nt = normalize_tag(w)
        if nt in known_tags:
            intent["tags"].append(nt)
        elif w in idx["words"] or len(w) < 3:
            intent["words"].append(w)
        else:
            fix = _close(nt, known_tags) or _close(w, idx["words"])
            if fix:
                intent["corrections"].append({"from": w, "to": fix})
                (intent["tags"] if fix in known_tags else intent["words"]).append(fix)
            else:
                intent["words"].append(w)
        i += 1
    intent["tags"] = list(dict.fromkeys(intent["tags"]))
    return intent


def _chips(intent: Dict[str, Any]) -> List[Dict[str, str]]:
    c = [{"kind": "tag", "label": t} for t in intent["tags"]]
    c += [{"kind": "word", "label": w} for w in intent["words"]]
    if intent["bpm_min"] is not None:
        lo, hi = intent["bpm_min"], intent["bpm_max"]
        c.append({"kind": "bpm", "label": f"{lo:.0f}–{hi:.0f} bpm" if hi - lo > 2 * EXACT_BPM_SLACK + .1 else f"{(lo + hi) / 2:.0f} bpm"})
    if intent["key"]:
        c.append({"kind": "key", "label": intent["key"]})
    if intent["sample_type"]:
        c.append({"kind": "type", "label": intent["sample_type"]})
    if intent.get("rating_min"):
        c.append({"kind": "rating", "label": f"★≥{intent['rating_min']}"})
    if intent.get("sent"):
        c.append({"kind": "sent", "label": "never sent" if intent["sent"] == "never" else "already sent"})
    return c


# ---------------------------------------------------------------- scoring
def _constraints(intent) -> List[Tuple[str, str, Any]]:
    cs = [("tag", t, t) for t in intent["tags"]] + [("word", w, w) for w in intent["words"]]
    if intent["bpm_min"] is not None:
        cs.append(("bpm", "bpm", (intent["bpm_min"], intent["bpm_max"])))
    if intent["key"]:
        cs.append(("key", intent["key"], intent["key"]))
    if intent["sample_type"]:
        cs.append(("type", intent["sample_type"], intent["sample_type"]))
    if intent.get("rating_min"):
        cs.append(("rating", f"★≥{intent['rating_min']}", intent["rating_min"]))
    if intent.get("sent"):
        cs.append(("sent", "never sent" if intent["sent"] == "never" else "already sent", intent["sent"]))
    return cs


def _satisfaction(t: Dict[str, Any], kind: str, val: Any) -> float:
    if kind == "tag":
        return 1.0 if val in t["tags"] else 0.0
    if kind == "word":
        if val in t["haystack"]:
            return 1.0
        best = max((difflib.SequenceMatcher(None, val, tok).ratio() for tok in t["name_tokens"] if abs(len(tok) - len(val)) <= 3),
                   default=0.0)
        return best * 0.8 if best >= 0.75 else 0.0
    if kind == "bpm":
        b = t["bpm"]
        if not b:
            return 0.2                                       # unknown tempo: not a match, but not a rejection
        lo, hi = val
        if lo <= b <= hi:
            return 1.0
        d = min(abs(b - lo), abs(b - hi))
        half_or_double = any(lo <= x <= hi for x in (b * 2, b / 2))
        return max(0.0, 1 - d / 20.0, 0.55 if half_or_double else 0.0)
    if kind == "key":
        return compatibility(t["camelot"], val) if t["camelot"] else 0.2
    if kind == "type":
        return 1.0 if t.get("sample_type") == val else 0.0
    if kind == "sent":
        return 1.0 if (t.get("sent_count", 0) == 0) == (val == "never") else 0.0
    if kind == "rating":
        r = t.get("rating")
        return 1.0 if r and r >= val else (0.3 if r else 0.0)
    return 0.0


def _score(t, cs) -> Tuple[float, bool, List[str], List[str]]:
    if not cs:
        return 0.0, True, [], []
    tot = got = 0.0
    exact, hit, miss = True, [], []
    for kind, label, val in cs:
        s = _satisfaction(t, kind, val)
        tot += WEIGHTS[kind]; got += WEIGHTS[kind] * s
        if s >= 0.999:
            hit.append(label)
        else:
            exact = False; miss.append(label)
    return got / tot, exact, hit, miss


# ---------------------------------------------------------------- public API
def search(q: str = "", tags: Optional[Iterable[str]] = None, tags_exclude: Optional[Iterable[str]] = None,
           bpm_min: Optional[float] = None, bpm_max: Optional[float] = None, key: Optional[str] = None,
           sample_type: Optional[str] = None, collection: Optional[str] = None, sort: str = "relevance",
           page: int = 1, per_page: int = 40, seed: Optional[int] = None, rating_min: Optional[int] = None,
           stack: bool = True, sent: Optional[str] = None,
           conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Always returns results when the library has any tracks. See module docstring."""
    with _session(conn) as db:
        idx = _index(db)
        pool = idx["tracks"]
        notes: List[str] = []
        if not pool:
            return {"mode": "empty-library", "total": 0, "exact_count": 0, "tracks": [], "chips": [], "notes":
                    ["Your library is empty — use “Import folder” to add your audio."],
                    "alternatives": [], "tag_summary": [], "corrections": [], "page": 1, "per_page": per_page}

        intent = parse_query(q, idx)
        for t in _normalize_tags(tags):
            if t not in intent["tags"]:
                intent["tags"].append(t)
        if bpm_min is not None or bpm_max is not None:
            intent["bpm_min"] = bpm_min if bpm_min is not None else 30; intent["bpm_max"] = bpm_max if bpm_max is not None else 300
        intent["key"] = to_camelot(key) or intent["key"]
        intent["sample_type"] = sample_type or intent["sample_type"]
        intent["rating_min"] = int(rating_min) if rating_min else None
        intent["sent"] = sent if sent in ("never", "sent") else None

        # collection scope + explicit exclusions are the only hard rules - and even they yield instead of returning nothing
        if collection:
            ids = {r[0] for r in db.execute("SELECT ct.track_id FROM collection_tracks ct JOIN collections c "
                                            "ON c.id = ct.collection_id WHERE c.name = ?", (collection,))}
            scoped = [t for t in pool if t["id"] in ids]
            if scoped:
                pool = scoped
            else:
                notes.append(f"Collection “{collection}” is empty, so I searched your whole library instead.")
        excl = set(_normalize_tags(tags_exclude))
        if excl:
            kept = [t for t in pool if not (t["tags"] & excl)]
            if kept:
                pool = kept
            else:
                notes.append("Excluding those tags would leave nothing, so the exclusion was ignored.")

        cs = _constraints(intent)
        scored = []
        for t in pool:
            s, exact, hit, miss = _score(t, cs)
            scored.append((s, exact, hit, miss, t))
        exact_count = sum(1 for x in scored if x[1]) if cs else len(scored)

        if not cs:                                           # empty query = discover
            rnd = random.Random(seed if seed is not None else 0)
            rnd.shuffle(scored)
            mode = "discover"
        else:
            scored.sort(key=lambda x: (-x[0], -len(x[4]["tags"]), x[4]["filename"].lower()))
            mode = "exact" if exact_count else "closest"

        if cs and 1 <= exact_count < 5:   # few exact hits: rank the rest by closeness to the best hit, not arbitrarily
            anchor = _signature(next(x for x in scored if x[1])[4]); acs = _constraints(anchor)
            head = [x for x in scored if x[1]]
            tail = sorted((x for x in scored if not x[1]),
                          key=lambda x: -(0.5 * x[0] + 0.5 * _score(x[4], acs)[0]))
            scored = head + tail
            notes.append("Only a few exact matches — the rest are tracks similar to the best one.")
        display = scored if sort == "relevance" else [x for x in scored if x[1]] if exact_count else scored[:200]
        keyfn = {"name": lambda x: x[4]["filename"].lower(), "bpm": lambda x: x[4]["bpm"] or 0,
                 "duration": lambda x: x[4]["duration_sec"] or 0, "newest": lambda x: x[4]["created_at"],
                 "key": lambda x: x[4]["camelot"] or ""}.get(sort)
        if keyfn:
            display = sorted(display, key=keyfn)
        elif sort == "random":
            display = random.Random(seed).sample(display, len(display))

        stacked = 0
        if stack:
            display, stacked = _collapse(display)
        per_page = max(1, min(int(per_page), 200)); page = max(1, int(page))
        window = display[(page - 1) * per_page: page * per_page]
        facet_src = [x for x in scored if x[1]] if exact_count else scored[:300]
        counts = Counter(g for x in facet_src for g in x[4]["tags"])
        summary = [{"tag": t, "count": 0} for t in excl] + [{"tag": t, "count": counts.get(t, 0)} for t in intent["tags"]]
        summary += [{"tag": g, "count": n} for g, n in counts.most_common(60) if g not in intent["tags"]]
        for e in summary:
            e["kind"] = tag_kind(e["tag"])

        result = {
            "mode": mode, "total": len(display) if exact_count or sort != "relevance" else len(scored), "exact_count": exact_count if cs else len(scored),
            "chips": _chips(intent), "corrections": intent["corrections"], "tag_summary": summary,
            "page": page, "per_page": per_page,
            "tracks": [_row(x, db, cs) for x in window],
        }
        if mode == "closest":
            notes.append(f"No track matches every part of “{q.strip() or 'your filters'}”, so here are the closest — "
                         f"best match is {round(scored[0][0] * 100)}%.")
        for c in intent["corrections"]:
            notes.append(f"Showing results for “{c['to']}” instead of “{c['from']}”.")
        lost = [w for w in intent["words"] if not any(w in t["haystack"] for t in idx["tracks"])]
        if lost:
            notes.append("Nothing in your library mentions " + ", ".join(f"“{w}”" for w in lost) + ".")
        if mode == "closest" and scored[0][0] < 0.35:
            notes.append("Nothing is really close to that — try a tag, a tempo like “120” or a track name; suggestions are below.")
        if stacked:
            result["stacked"] = stacked
        result["notes"] = notes
        result["alternatives"] = _alternatives(scored, cs, intent) if cs and exact_count < 5 else []
        return result


def _collapse(display):
    """Show each version group once (its best-matching member, preferring the primary on a near-tie). Returns (rows, hidden_count)."""
    primary_at = {}
    for x in display:
        g = x[4].get("group")
        if g and g["is_primary"]:
            primary_at[g["id"]] = x
    seen, out, hidden = set(), [], 0
    for x in display:
        g = x[4].get("group")
        if not g:
            out.append(x); continue
        if g["id"] in seen:
            hidden += 1; continue
        seen.add(g["id"])
        p = primary_at.get(g["id"])
        out.append(p if p is not None and p[0] >= x[0] - 0.01 else x)
    return out, hidden


def _row(x, db, cs) -> Dict[str, Any]:
    s, exact, hit, miss, t = x
    return {"id": t["id"], "filename": t["filename"], "path": t["path"], "bpm": t["bpm"], "bpm_source": t.get("bpm_source"),
            "key": t["key"], "camelot_key": t["camelot"], "duration_sec": t["duration_sec"], "format": t["format"],
            "sample_type": t.get("sample_type"), "tags": sorted(t["tags"]),
            "rating": t.get("rating"), "notes": t.get("notes"), "overridden": t.get("overridden", []),
            "group": t.get("group"), "sent_count": t.get("sent_count", 0), "auto_tags": sorted(t.get("auto_tags", ())),
            "created_at": t.get("created_at"),
            "match": {"pct": round(s * 100) if cs else None, "exact": exact, "matched": hit, "missed": miss}}


def _alternatives(scored, cs, intent) -> List[Dict[str, Any]]:
    """'Try instead' chips: for each constraint, how many tracks would match if it were dropped."""
    out = []
    for i, (kind, label, val) in enumerate(cs):
        rest = cs[:i] + cs[i + 1:]
        if not rest:
            continue
        n = sum(1 for x in scored if _score(x[4], rest)[1])
        if n:
            out.append({"drop": label, "kind": kind, "count": n, "label": f"without “{label}” ({n})"})
    out.sort(key=lambda a: -a["count"])
    return out[:4]


# ---------------------------------------------------------------- suggestions & 'more like this'
def autocomplete(prefix: str, conn: Optional[sqlite3.Connection] = None, limit: int = 12) -> Dict[str, Any]:
    """Suggestions for the search box. Always returns something, even for gibberish or an empty box."""
    with _session(conn) as db:
        idx = _index(db)
        p = (prefix or "").strip().lower()
        last = re.split(r"\s+", p)[-1] if p else ""
        items: List[Dict[str, Any]] = []
        tc = idx["tag_counts"]
        if not idx["tracks"]:
            return {"items": [{"type": "hint", "label": "Index a folder to start searching your library"}]}

        if last.isdigit() and 30 <= int(last) <= 300:
            b = int(last)
            n = sum(1 for t in idx["tracks"] if t["bpm"] and abs(t["bpm"] - b) <= EXACT_BPM_SLACK)
            items.append({"type": "bpm", "label": f"{b} bpm", "detail": f"{n} tracks within ±{EXACT_BPM_SLACK:.0f}", "insert": f"{b}bpm"})
            n2 = sum(1 for t in idx["tracks"] if t["bpm"] and abs(t["bpm"] - b) <= 10)
            items.append({"type": "bpm", "label": f"{b - 10}–{b + 10} bpm", "detail": f"{n2} tracks", "insert": f"{b - 10}-{b + 10}"})
        if to_camelot(last):
            items.append({"type": "key", "label": f"key {to_camelot(last)}", "detail": "Camelot", "insert": to_camelot(last)})
        if last:
            for tag, n in sorted(((t, n) for t, n in tc.items() if t.startswith(last)), key=lambda x: -x[1])[:5]:
                items.append({"type": "tag", "label": tag, "detail": f"{n} tracks", "insert": tag})
            if len(last) >= 3 and not any(i["type"] == "tag" for i in items):
                fix = _close(normalize_tag(last), tc) or _close(last, idx["words"])
                if fix:
                    items.append({"type": "tag" if fix in tc else "word", "label": f"Did you mean “{fix}”?",
                                  "detail": f"{tc.get(fix, idx['words'].get(fix, 0))} tracks", "insert": fix})
            hits = [t for t in idx["tracks"] if last in t["filename"].lower()] if len(last) >= 2 and not last.isdigit() else []
            hits.sort(key=lambda t: (not t["filename"].lower().startswith(last), len(t["filename"])))
            for t in hits[:4]:
                items.append({"type": "track", "id": t["id"], "label": t["filename"], "detail": "more like this →"})
        if len(items) < 4:   # never leave the dropdown empty: offer the library's own popular facets
            for tag, n in tc.most_common(6):
                if tag not in {i.get("insert") for i in items}:
                    items.append({"type": "tag", "label": tag, "detail": f"{n} tracks · popular", "insert": tag})
            bands = Counter(int(t["bpm"] // 10 * 10) for t in idx["tracks"] if t["bpm"])
            for b, n in bands.most_common(3):
                items.append({"type": "bpm", "label": f"{b}–{b + 10} bpm", "detail": f"{n} tracks · popular", "insert": f"{b}-{b + 10}"})
        items.append({"type": "surprise", "label": "Surprise me", "detail": "random picks", "insert": ""})
        return {"items": items[:limit], "corrections": []}


def _signature(ref: Dict[str, Any]) -> Dict[str, Any]:
    """Intent that describes a track: its non-generic tags, tempo window and key."""
    rich = [t for t in ref["tags"] if not re.fullmatch(r"\d{4}|beat|sample|record", t)]
    intent = {"tags": rich or sorted(ref["tags"]), "words": [], "bpm_min": None, "bpm_max": None,
              "key": ref["camelot"], "sample_type": None, "corrections": []}
    if ref["bpm"]:
        intent["bpm_min"], intent["bpm_max"] = ref["bpm"] - 4, ref["bpm"] + 4
    return intent


def _coverage(db) -> Dict[str, int]:
    from .analysis_job import coverage          # lazy: analysis_job imports this module
    return coverage(db)


def _audio_rows(db, idx, hits, offset, per_page):
    rows = []
    for h in hits[offset: offset + per_page]:
        t = idx["by_id"].get(h["id"])
        if t is None:
            continue
        row = _row((h["score"], False, [], [], t), db, [True])
        row["match"].update(pct=sim.pct(h["score"]), why=sim.why(h["groups"]))
        rows.append(row)
    return rows


def _audio_response(db, idx, hits, weights, chips, notes, page, per_page, by="audio"):
    cov = _coverage(db)
    if cov["pending"]:
        notes = notes + [f"{cov['analyzed']:,} of {cov['total']:,} tracks are analyzed for sound; the rest can't show up here yet."]
    return {"mode": "similar", "by": by, "groups": sorted(weights), "total": len(hits), "exact_count": 0, "chips": chips,
            "tag_summary": [], "page": page, "per_page": per_page, "corrections": [], "alternatives": [], "notes": notes,
            "analysis": cov, "tracks": _audio_rows(db, idx, hits, (page - 1) * per_page, per_page)}


def similar_to(track_id: int, per_page: int = 40, conn: Optional[sqlite3.Connection] = None, by: str = "auto",
               groups: Optional[Sequence[str]] = None, page: int = 1) -> Dict[str, Any]:
    """
    'More like this'. by='audio' / 'auto' (default): tracks that SOUND closest (timbre, spectrum, pitch, energy, rhythm - toggle
    with `groups`). Falls back to tags + tempo + key when the track isn't analyzed yet, or when by='tags'.
    """
    with _session(conn) as db:
        idx = _index(db)
        ref = idx["by_id"].get(track_id)
        if ref is None:
            res = search("", per_page=per_page, conn=db)
            res["notes"] = [f"Track {track_id} isn't in the library, so here are some picks to explore."] + res["notes"]
            return res
        weights = sim.parse_weights(groups)
        if by != "tags" and sim.has_vector(db, track_id):
            hits = sim.neighbours(db, track_id=track_id, weights=weights, k=1000)
            own = ref.get("group_id")
            if own is not None:                       # its own versions would trivially top the list
                hits = [h for h in hits if idx["by_id"].get(h["id"], {}).get("group_id") != own]
            names = ", ".join(sorted(weights, key=lambda g: -weights[g]))
            return _audio_response(db, idx, hits, weights, [{"kind": "similar", "label": f"sounds like {ref['filename']}"}],
                                   [f"Tracks that sound closest to “{ref['filename']}” — matched on {names}."], page, per_page)
        # ---- metadata fallback ----
        intent = _signature(ref)
        cs = _constraints(intent)
        scored = sorted(((*_score(t, cs), t) for t in idx["tracks"] if t["id"] != track_id),
                        key=lambda x: (-x[0], x[4]["filename"].lower()))
        window = scored[(page - 1) * per_page: page * per_page]
        notes = [f"Tracks closest to “{ref['filename']}” by tags, tempo and key."]
        if by != "tags":
            notes.append("This track hasn't been analyzed for sound yet, so these are tag/tempo/key matches. Analyze your library to match by sound.")
        return {"mode": "similar", "by": "tags", "groups": [], "total": len(scored), "exact_count": sum(1 for x in scored if x[1]),
                "chips": [{"kind": "similar", "label": f"like {ref['filename']}"}] + _chips(intent),
                "tag_summary": [], "page": page, "per_page": per_page, "corrections": [], "alternatives": [],
                "notes": notes, "needs_analysis": by != "tags", "analysis": _coverage(db),
                "tracks": [_row(x, db, cs) for x in window]}


def sound_search(query_vec, clip_info: Optional[Dict[str, Any]] = None, groups: Optional[Sequence[str]] = None,
                 per_page: int = 60, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """'Mimic': the library tracks that sound closest to a clip the user recorded or dropped in. Never empty."""
    with _session(conn) as db:
        idx = _index(db)
        weights = sim.parse_weights(groups)
        hits = sim.neighbours(db, query_vec=query_vec, weights=weights, k=per_page)
        if not hits:
            res = search("", per_page=per_page, conn=db)
            res["notes"] = ["Mimic needs your library analyzed for sound first — start the analysis from the sidebar. Meanwhile, here are some picks."] + res["notes"]
            res["needs_analysis"] = True; res["analysis"] = _coverage(db)
            return res
        notes = ["Closest to your clip by overall sound — tone, brightness, energy and tempo. It doesn't match melodies note-for-note."]
        if clip_info:
            bits = []
            if clip_info.get("bpm"):
                bits.append(f"~{clip_info['bpm']:.0f} bpm")
            if clip_info.get("key") and clip_info.get("key_conf", 0) >= 0.5:
                bits.append(f"key {clip_info['key']}")
            if bits:
                notes.append("Your clip sounds like " + " · ".join(bits) + ".")
        return _audio_response(db, idx, hits, weights, [{"kind": "clip", "label": "your clip"}], notes, 1, per_page, by="clip")
