# audio_engine/sends.py
"""
Send tracker: who has heard what, and who should hear this next.

Version-aware: if you sent "FINAL v2" to someone, the app knows they've heard that track, so it won't suggest "v1" of
the same beat to them (the stack counts as one track). Recipients come from the `people` table (your CRM import).
Ranking uses what your contacts actually have - genre/mood words in their tags and their tier - and says why.
"""
import json
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from . import smart_search
from .library import _normalize_tags, _now, _session, add_or_update_person, normalize_tag

TIER_WEIGHT = {1: 3.0, 2: 2.0, 3: 1.0, 4: 1.5}      # tier 5 = blacklist, never suggested
TIER_NAME = {1: "inner circle", 2: "close", 3: "contact", 4: "worth pitching", 5: "blacklist"}
MATCH_WEIGHT = 4.0


def _plain_tags(person_row) -> set:
    """A contact's genre/taste words ('trap', 'rnb'); 'role:artist' / 'loc:nyc' labels aren't taste, so they're skipped."""
    return {t for t in json.loads(person_row["tags_json"] or "[]") if ":" not in t}


def _person_view(r: sqlite3.Row, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    tags = json.loads(r["tags_json"] or "[]")
    d = {"id": r["id"], "name": r["name"], "email": r["email"], "tier": r["tier"], "status": r["status"], "role": r["role"],
         "location": r["location"], "company": r["company"], "notes": r["notes"], "tags": tags,
         "taste": [t for t in tags if ":" not in t], "inactive": (r["status"] or "").strip().lower() == "inactive" or r["tier"] == 5}
    d.update(extra or {})
    return d


def list_people(q: Optional[str] = None, limit: int = 40, offset: int = 0, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    with _session(conn) as db:
        like = f"%{(q or '').strip().lower()}%"
        where, params = ("WHERE LOWER(p.name) LIKE ? OR LOWER(COALESCE(p.email,'')) LIKE ? OR LOWER(p.tags_json) LIKE ? OR LOWER(COALESCE(p.company,'')) LIKE ?", [like] * 4) if q else ("", [])
        total = db.execute(f"SELECT COUNT(*) FROM people p {where}", params).fetchone()[0]
        rows = db.execute(f"SELECT p.*, COUNT(s.id) AS n_sends, MAX(s.sent_at) AS last_sent FROM people p LEFT JOIN sends s ON s.person_id = p.id "
                          f"{where} GROUP BY p.id ORDER BY COALESCE(p.tier, 9), LOWER(p.name) LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        return {"total": total, "people": [_person_view(r, {"n_sends": r["n_sends"], "last_sent": r["last_sent"]}) for r in rows]}


def save_person(fields: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> int:
    """Create or edit a contact. With an `id` it edits that row (including renames); without, it matches on email/name."""
    name = (fields.get("name") or "").strip()
    if not name:
        raise ValueError("A contact needs a name")
    tier = fields.get("tier")
    tier = int(tier) if tier not in (None, "") else None
    if tier is not None and not 1 <= tier <= 5:
        raise ValueError("Tier is 1 (inner circle) to 5 (blacklist)")
    tags = fields.get("tags")
    tags = _normalize_tags(tags if isinstance(tags, list) else re.split(r"[,;\n]", tags or ""))
    with _session(conn) as db:
        if fields.get("id"):
            pid = int(fields["id"])
            if not db.execute("SELECT 1 FROM people WHERE id = ?", (pid,)).fetchone():
                raise ValueError("No such contact")
            email = (fields.get("email") or "").strip() or None
            db.execute("UPDATE people SET name=?, email=?, tags_json=?, role=?, tier=?, location=?, company=?, notes=?, status=?, updated_at=? WHERE id=?",
                       (name, email, json.dumps(tags), fields.get("role") or None, tier, fields.get("location") or None,
                        fields.get("company") or None, fields.get("notes") or None, fields.get("status") or None, _now(), pid))
            return pid
        return add_or_update_person(name, fields.get("email"), tags, role=fields.get("role"), tier=tier, location=fields.get("location"),
                                    company=fields.get("company"), notes=fields.get("notes"), status=fields.get("status"), replace_tags=True, conn=db)


def record_sends(track_ids: Iterable[int], person_id: int, note: Optional[str] = None,
                 conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Log that these tracks were sent to this person. A track already logged for them is skipped (reported, not duplicated)."""
    ids = list(dict.fromkeys(int(i) for i in track_ids))
    if not ids:
        raise ValueError("No tracks selected")
    with _session(conn) as db:
        p = db.execute("SELECT name FROM people WHERE id = ?", (person_id,)).fetchone()
        if p is None:
            raise ValueError("No such contact")
        made, skipped = [], []
        for tid in ids:
            if not db.execute("SELECT 1 FROM tracks WHERE id = ?", (tid,)).fetchone():
                continue
            if db.execute("SELECT 1 FROM sends WHERE track_id = ? AND person_id = ?", (tid, person_id)).fetchone():
                skipped.append(tid); continue
            made.append(db.execute("INSERT INTO sends (track_id, person_id, sent_at, note) VALUES (?,?,?,?)", (tid, person_id, _now(), note)).lastrowid)
    smart_search.invalidate()
    return {"send_ids": made, "skipped": skipped, "person": p["name"]}


def delete_sends(send_ids: Iterable[int], conn: Optional[sqlite3.Connection] = None) -> int:
    ids = [int(i) for i in send_ids]
    with _session(conn) as db:
        n = sum(db.execute("DELETE FROM sends WHERE id = ?", (i,)).rowcount for i in ids)
    smart_search.invalidate()
    return n


def _group_members(db, track_id: int) -> List[int]:
    r = db.execute("SELECT group_id FROM version_members WHERE track_id = ?", (track_id,)).fetchone()
    if not r:
        return [track_id]
    return [x[0] for x in db.execute("SELECT track_id FROM version_members WHERE group_id = ?", (r[0],))]


def sends_for_track(track_id: int, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Everyone who got this track - or another version of it (flagged via_version)."""
    with _session(conn) as db:
        members = _group_members(db, track_id)
        q = ",".join("?" * len(members))
        rows = db.execute(f"SELECT s.id, s.track_id, s.sent_at, s.note, p.id AS person_id, p.name, t.filename FROM sends s "
                          f"JOIN people p ON p.id = s.person_id JOIN tracks t ON t.id = s.track_id "
                          f"WHERE s.track_id IN ({q}) ORDER BY s.sent_at DESC", members).fetchall()
        return [{**dict(r), "via_version": r["track_id"] != track_id} for r in rows]


def suggest_recipients(track_id: int, top_n: int = 8, include_sent: bool = False,
                       conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Who to send this track to: shared taste words + tier, skipping people who already heard it (or a version of it)."""
    with _session(conn) as db:
        idx = smart_search._index(db)
        t = idx["by_id"].get(track_id)
        if t is None:
            raise ValueError("No such track")
        members = _group_members(db, track_id)
        sent_to = {r[0] for r in db.execute(f"SELECT person_id FROM sends WHERE track_id IN ({','.join('?' * len(members))})", members)}
        ranked, matched_any = [], False
        for r in db.execute("SELECT * FROM people"):
            if r["tier"] == 5 or (r["status"] or "").strip().lower() == "inactive":
                continue
            heard = r["id"] in sent_to
            if heard and not include_sent:
                continue
            matched = sorted(_plain_tags(r) & t["tags"])
            matched_any = matched_any or bool(matched)
            score = MATCH_WEIGHT * len(matched) + TIER_WEIGHT.get(r["tier"], 0.5)
            reasons = ([f"likes {', '.join(matched)}"] if matched else []) + ([TIER_NAME[r["tier"]]] if r["tier"] else []) + \
                      ([r["role"].lower()] if r["role"] else [])
            ranked.append(_person_view(r, {"score": round(score, 2), "matched": matched, "reasons": reasons, "already_heard": heard}))
        ranked.sort(key=lambda x: (-x["score"], x["tier"] or 99, x["name"].lower()))
        note = None if matched_any else ("None of your contacts have genre or mood words in their tags that match this track, so this is ranked by "
                                         "tier. Add taste tags to contacts (e.g. “trap”, “rnb”) to sharpen it.")
        return {"track_id": track_id, "recipients": ranked[:top_n], "note": note, "already_sent_to": len(sent_to)}


def suggest_tracks_for_person(person_id: int, top_n: int = 12, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """What to send this person next: tracks they haven't heard (in any version), best taste match + rated + not yet pitched anywhere."""
    with _session(conn) as db:
        p = db.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        if p is None:
            raise ValueError("No such contact")
        idx = smart_search._index(db)
        heard = {r[0] for r in db.execute("SELECT track_id FROM sends WHERE person_id = ?", (person_id,))}
        heard_groups = {idx["by_id"][i]["group_id"] for i in heard if i in idx["by_id"] and idx["by_id"][i].get("group_id")}
        taste = _plain_tags(p)
        out = []
        for t in idx["tracks"]:
            g = t.get("group")
            if t["id"] in heard or (g and (g["id"] in heard_groups or not g["is_primary"])):
                continue
            matched = sorted(taste & t["tags"])
            score = MATCH_WEIGHT * len(matched) + {5: 2.0, 4: 1.5, 3: 0.5}.get(t.get("rating") or 0, 0) \
                + (1.0 if "beat" in t["tags"] else 0) + (0.5 if t.get("sent_count", 0) == 0 else 0)
            reasons = ([f"{', '.join(matched)} fits their taste"] if matched else []) + ([f"{t['rating']}★"] if t.get("rating") else []) + \
                      (["never pitched"] if t.get("sent_count", 0) == 0 else [])
            out.append((score, t.get("created_at") or "", t, reasons))
        out.sort(key=lambda x: x[1], reverse=True)          # newest first ...
        out.sort(key=lambda x: -x[0])                        # ... then best score first (stable, so newest breaks ties)
        rows = [{**smart_search._row((s, False, [], [], t), db, []), "reasons": rs, "score": round(s, 2)} for s, _, t, rs in out[:top_n]]
        note = None if taste else "This contact has no genre/taste tags yet, so suggestions lean on your ratings and what you haven't pitched."
        return {"person": _person_view(p), "tracks": rows, "note": note, "already_heard": len(heard)}
