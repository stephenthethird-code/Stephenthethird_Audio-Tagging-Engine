#!/usr/bin/env python3
"""
End-to-end smoke test for Audio Converter v2, driven through the real HTTP API.

  AUDIO_LIBRARY_DB=/tmp/smoke.sqlite ./audio-web-v2/run.sh        # 1) start a server on a THROWAWAY database
  python3 scripts/smoke_test.py --folder /path/to/audio           # 2) point this at a folder with ~20-100 audio files

It imports the folder, runs sound analysis, then exercises search, "sounds like", Mimic, batch edit + undo, version stacks,
moods, genres, the send tracker, Discover shelves and error handling. It WRITES to the library (tags, contacts, edits), so it
refuses to run against a non-empty library unless --force. Needs only the Python standard library.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

PASS, FAIL = [], []


def call(base, method, path, body=None, raw=False, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            blob = r.read()
            return (r.status, blob) if raw else (r.status, json.loads(blob or b"null"))
    except urllib.error.HTTPError as e:
        blob = e.read()
        try:
            return e.code, json.loads(blob)
        except ValueError:
            return e.code, blob


def multipart(base, path, filename, content, fields):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(), content, b"\r\n"]
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(base + path, data=b"".join(parts), method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def section(title):
    print(f"\n== {title}")


def wait(fn, timeout, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8766")
    ap.add_argument("--folder", required=True, help="folder of audio files to import")
    ap.add_argument("--timeout", type=int, default=1200, help="seconds to wait for import/analysis")
    ap.add_argument("--force", action="store_true", help="run even if the library already has tracks")
    a = ap.parse_args()
    B = a.url.rstrip("/")
    t_start = time.time()

    section("Server + safety")
    st, stats = call(B, "GET", "/api/stats")
    if not check("server reachable", st == 200, B):
        return 2
    if stats["tracks"] and not a.force:
        print(f"\n  The library already has {stats['tracks']} tracks. This test edits the library - run it on a throwaway database, or pass --force.")
        return 2

    section("Import wizard")
    st, d = call(B, "GET", "/api/fs?path=" + urllib.request.quote(a.folder))
    check("folder picker lists the folder", st == 200 and "dirs" in d)
    st, scan = call(B, "POST", "/api/import/scan", {"path": a.folder})
    check("scan previews files (writes nothing)", st == 200 and scan["total"] > 0, f"{scan.get('total')} files, {scan.get('new')} new, {scan.get('by_format')}")
    if not scan.get("total"):
        return 2
    st, s2 = call(B, "GET", "/api/stats"); check("scan did not touch the library", s2["tracks"] == 0)
    st, job = call(B, "POST", "/api/import/start", {"path": a.folder, "deep": True})
    t0 = time.time()
    fin = wait(lambda: (lambda r: r if r["state"] in ("done", "error", "cancelled") else None)(call(B, "GET", f"/api/import/{job['id']}")[1]), a.timeout, 0.5)
    check("import finishes", bool(fin) and fin["state"] == "done", f"{fin and fin['counts']} in {time.time() - t0:.1f}s")
    check("every file imported, none failed", fin["counts"]["added"] == scan["total"] and fin["counts"]["failed"] == 0)
    check("auto-tagging ran", fin["tag_stats"].get("tags_added", 0) > 0, f"{fin['tag_stats'].get('tags_added')} tags, {fin['tag_stats'].get('bpm_filled')} tempos from names")
    n = scan["total"]

    section("Sound analysis (background job)")
    t0 = time.time()
    ok = wait(lambda: (lambda r: r if (not r["job"] or r["job"]["state"] != "running") and r["pending"] == 0 and r["total"] == n else None)(call(B, "GET", "/api/analysis")[1]), a.timeout, 1.0)
    cov = call(B, "GET", "/api/analysis")[1]
    check("analysis completes", bool(ok), f"{cov['analyzed']} analyzed, {cov['failed']} unreadable, {time.time() - t0:.0f}s ({(time.time() - t0) / max(1, n):.2f}s/track)")
    check("≥ 90% of tracks analyzed", cov["analyzed"] >= 0.9 * n, f"{cov['analyzed']}/{n}")
    st, stats = call(B, "GET", "/api/stats")
    check("key detected for analyzed tracks", stats["with_key"] >= cov["analyzed"], f"{stats['with_key']} with key")
    check("tempo available for most tracks", stats["with_bpm"] >= 0.9 * n, f"{stats['with_bpm']}/{n}")

    section("Search never dead-ends")
    lat = []
    for q in ["", "dark piano 140", "trapp 2024", "!!!", "xylophone 8A 300bpm", "a", "zzzzqq", "   ", "120-130", "A minor loop", "🎵"]:
        t = time.time(); st, r = call(B, "GET", "/api/search?q=" + urllib.request.quote(q)); lat.append(time.time() - t)
        check(f"query {q!r} returns tracks", st == 200 and len(r["tracks"]) > 0, f"{r.get('mode')}, {len(r['tracks'])} rows")
    check("search is fast", max(lat) < 2.0, f"max {max(lat) * 1000:.0f} ms, avg {sum(lat) / len(lat) * 1000:.0f} ms")
    st, disc = call(B, "GET", "/api/search?per_page=5"); check("tag facets present", len(disc["tag_summary"]) > 0, f"{len(disc['tag_summary'])} tags")
    ids = [t["id"] for t in call(B, "GET", f"/api/search?per_page={min(n, 200)}&stack=0")[1]["tracks"]]
    first = ids[0]

    section("Sounds like + Mimic")
    st, sim = call(B, "GET", f"/api/similar/{first}")
    check("sounds-like uses the audio", st == 200 and sim["by"] == "audio" and len(sim["tracks"]) > 0, f"{len(sim['tracks'])} results, top {sim['tracks'][0]['match']['pct']}%" if sim["tracks"] else "")
    check("never lists the track itself", first not in [t["id"] for t in sim["tracks"]])
    st, only = call(B, "GET", f"/api/similar/{first}?groups=timbre"); check("feature groups switch", only.get("groups") == ["timbre"])
    st, blob = call(B, "GET", f"/api/audio/{first}", raw=True)
    check("preview audio streams", st == 200 and len(blob) > 1000, f"{len(blob) // 1024} KB")
    req = urllib.request.Request(B + f"/api/audio/{first}", headers={"Range": "bytes=0-99"})
    try:
        with urllib.request.urlopen(req) as r: check("seeking works (HTTP Range)", r.status == 206)
    except Exception as e: check("seeking works (HTTP Range)", False, str(e))
    st, m = multipart(B, "/api/mimic", "clip.audio", blob, {"groups": ""})
    top = m["tracks"][0] if st == 200 and m.get("tracks") else {}
    grp = (top.get("group") or {}).get("id")
    own_grp = next((t.get("group", {}) or {} for t in call(B, "GET", f"/api/search?per_page={n}&stack=0")[1]["tracks"] if t["id"] == first), {}).get("id")
    check("Mimic finds the clip's source track first", top.get("id") == first or (grp is not None and grp == own_grp), f"top: {top.get('filename')} {top.get('match', {}).get('pct')}%")
    st, bad = multipart(B, "/api/mimic", "x.txt", b"not audio", {})
    check("Mimic rejects a non-audio file cleanly", st == 400, str(bad.get("detail"))[:60])

    section("Editing: batch, inspector, undo")
    three = ids[:3]
    st, r = call(B, "POST", "/api/batch", {"ids": three, "add_tags": ["smoke test"], "set": {"rating": 5, "bpm": 99}})
    check("batch edit applies", st == 200 and r["tracks"] == 3, str(r))
    rows = {t["id"]: t for t in call(B, "GET", "/api/search?tag=smoke%20test&stack=0&per_page=50")[1]["tracks"]}
    check("edits visible in search (tag, 5★, bpm 99 marked manual)", all(rows[i]["rating"] == 5 and rows[i]["bpm"] == 99 and rows[i]["bpm_source"] == "manual" for i in three))
    st, det = call(B, "GET", f"/api/tracks/{three[0]}")
    check("inspector shows detected vs yours", det["user"]["bpm"] == 99 and det["detected"]["bpm"] != 99 or det["detected"]["bpm"] is None, f"detected {det['detected']['bpm']}, yours {det['user']['bpm']}")
    st, u = call(B, "POST", f"/api/undo/{r['batch_id']}", {})
    after = {t["id"]: t for t in call(B, "GET", "/api/search?stack=0&per_page=200")[1]["tracks"]}
    check("undo restores everything", st == 200 and all(after[i]["rating"] is None and "smoke test" not in after[i]["tags"] and after[i]["bpm"] != 99 for i in three))
    check("can't undo twice", call(B, "POST", f"/api/undo/{r['batch_id']}", {})[0] == 400)
    check("invalid BPM rejected (400)", call(B, "POST", "/api/batch", {"ids": [first], "set": {"bpm": 5}})[0] == 400)
    check("unknown folder rejected (400)", call(B, "POST", "/api/import/scan", {"path": "/no/such/folder"})[0] == 400)
    check("missing track audio is a clean 404", call(B, "GET", "/api/audio/99999999", raw=True)[0] == 404)
    st, sv = call(B, "POST", "/api/saved-searches", {"name": "smoke", "query": {"q": "x"}}); check("saved search create", st == 200)
    check("saved search list + delete", len(call(B, "GET", "/api/saved-searches")[1]) == 1 and call(B, "DELETE", f"/api/saved-searches/{sv['id']}")[0] == 200)
    check("collection add", call(B, "POST", "/api/collections/Smoke/tracks", {"ids": three})[1].get("added") == 3)

    section("Version stacks")
    st, g = call(B, "GET", "/api/versions/groups?limit=100")
    check("stack detection runs", st == 200, f"{g['total']} groups ({sum(1 for x in g['groups'] if x['kind'] == 'duplicate')} same audio)")
    stacked = call(B, "GET", f"/api/search?per_page=1&stack=1")[1]; flat = call(B, "GET", "/api/search?per_page=1&stack=0")[1]
    check("stacking hides repeats; unstacking shows all", flat["total"] == n and (stacked["total"] < n if g["total"] else stacked["total"] == n), f"{stacked['total']} stacked vs {flat['total']} flat")
    if g["total"]:
        grp = g["groups"][0]; pid = grp["primary_id"]; other = next(m for m in grp["members"] if m["id"] != pid)
        check("group has a primary shown on top", sum(1 for m in grp["members"] if m["is_primary"]) == 1)
        call(B, "POST", f"/api/versions/{grp['id']}/primary", {"track_id": other["id"]})
        check("'Make main' sticks and locks the group", call(B, "GET", f"/api/versions/group/{grp['id']}")[1]["primary_id"] == other["id"])
        call(B, "POST", "/api/versions/rebuild", {})
        check("…and survives a rescan", call(B, "GET", f"/api/versions/group/{grp['id']}")[1]["primary_id"] == other["id"])

    section("Moods + genres")
    if cov["analyzed"] >= 20:
        mood_tracks = [t for t in call(B, "GET", f"/api/search?stack=0&per_page={min(n, 200)}")[1]["tracks"] if t["auto_tags"]]
        check("moods tagged from audio (✦)", len(mood_tracks) > 0, f"{len(mood_tracks)} tracks, e.g. {mood_tracks[0]['auto_tags'] if mood_tracks else ''}")
        if mood_tracks:
            t = mood_tracks[0]; tag = t["auto_tags"][0]
            call(B, "POST", "/api/batch", {"ids": [t["id"]], "remove_tags": [tag]}); call(B, "POST", "/api/moods/apply", {})
            again = next(x for x in call(B, "GET", "/api/search?stack=0&per_page=200")[1]["tracks"] if x["id"] == t["id"])
            check("a removed mood is never re-added", tag not in again["tags"])
    else:
        print("  (skipped: moods need at least 20 analyzed tracks)")
    by_bpm = sorted([t for t in call(B, "GET", f"/api/search?stack=0&per_page={min(n, 200)}")[1]["tracks"] if t["bpm"]], key=lambda t: t["bpm"])
    if len(by_bpm) >= 12:
        call(B, "POST", "/api/batch", {"ids": [t["id"] for t in by_bpm[:3]], "add_tags": ["house"]}); call(B, "POST", "/api/batch", {"ids": [t["id"] for t in by_bpm[-3:]], "add_tags": ["trap"]})
        ov = call(B, "GET", "/api/genres")[1]
        check("genre overview counts labeled tracks", {x["genre"] for x in ov["genres"]} >= {"house", "trap"} and ov["labeled"] >= 6)
        st, ty = call(B, "GET", "/api/search?q=trapp")
        check("typo 'trapp' is corrected to 'trap' and says so", ty["corrections"] == [{"from": "trapp", "to": "trap"}] and any("instead of" in x for x in ty["notes"]), str(ty["corrections"]))
        sg = call(B, "GET", "/api/genres/suggestions?min_conf=0.3")[1]
        check("genre suggestions come back (or say why not)", bool(sg["suggestions"]) or bool(sg.get("reason")), f"{len(sg['suggestions'])} suggestions")
        if sg["suggestions"]:
            s0 = sg["suggestions"][0]; acc = call(B, "POST", "/api/genres/accept", {"items": [{"track_id": s0["track_id"], "genre": s0["genre"]}]})[1]
            row = next(x for x in call(B, "GET", f"/api/search?tag={s0['genre']}&stack=0&per_page=200")[1]["tracks"] if x["id"] == s0["track_id"])
            check("accepted genre becomes an auto tag", s0["genre"] in row["auto_tags"])
            for b in acc["batch_ids"]: call(B, "POST", f"/api/undo/{b}", {})
            call(B, "POST", "/api/genres/reject", {"items": [{"track_id": s0["track_id"], "genre": s0["genre"]}]})
            check("declined suggestion does not return", all((x["track_id"], x["genre"]) != (s0["track_id"], s0["genre"]) for x in call(B, "GET", "/api/genres/suggestions?min_conf=0.3")[1]["suggestions"]))

    section("Send tracker")
    st, pid = call(B, "POST", "/api/people", {"name": "Smoke Test Contact", "tier": 2, "tags": "role:artist, trap, dark"})
    pid = pid["id"]
    target = by_bpm[-1]["id"] if by_bpm else first
    st, rec = call(B, "GET", f"/api/tracks/{target}/send-suggestions")
    check("recipient suggestions include the contact, with reasons", any(r["id"] == pid for r in rec["recipients"]), str([r["reasons"] for r in rec["recipients"] if r["id"] == pid]))
    st, sent = call(B, "POST", "/api/sends", {"track_ids": [target], "person_id": pid})
    check("mark sent", st == 200 and len(sent["send_ids"]) == 1)
    check("duplicate send is skipped, not doubled", call(B, "POST", "/api/sends", {"track_ids": [target], "person_id": pid})[1]["skipped"] == [target])
    check("contact no longer suggested for it", all(r["id"] != pid for r in call(B, "GET", f"/api/tracks/{target}/send-suggestions")[1]["recipients"]))
    row = next(x for x in call(B, "GET", "/api/search?stack=0&per_page=200")[1]["tracks"] if x["id"] == target)
    check("sent badge count on the track", row["sent_count"] == 1)
    nxt = call(B, "GET", f"/api/people/{pid}/suggestions")[1]
    check("'what to send' excludes what they heard", all(t["id"] != target for t in nxt["tracks"]) and len(nxt["tracks"]) > 0, f"{len(nxt['tracks'])} suggestions")
    grp_of = (row.get("group") or {}).get("id")
    if grp_of:
        mem = [m for m in call(B, "GET", f"/api/versions/group/{grp_of}")[1]["members"] if m["id"] != target]
        if mem:
            check("version-aware: other versions count as heard", all(r["id"] != pid for r in call(B, "GET", f"/api/tracks/{mem[0]['id']}/send-suggestions")[1]["recipients"]) and call(B, "GET", f"/api/tracks/{mem[0]['id']}/sends")[1][0]["via_version"])
    check("remove send (undo)", call(B, "POST", "/api/sends/delete", {"ids": sent["send_ids"]})[1]["deleted"] == 1)
    check("people list + search", call(B, "GET", "/api/people?q=smoke")[1]["total"] == 1)

    section("Discover home")
    st, sh = call(B, "GET", "/api/shelves")
    check("shelves built from the library", st == 200 and len(sh["shelves"]) >= 3, ", ".join(s["id"] for s in sh["shelves"]))
    check("every track shelf has tracks", all(s["items"] for s in sh["shelves"] if s["type"] == "tracks"))
    call(B, "POST", "/api/batch", {"ids": ids[:3], "set": {"rating": 5}})
    check("'sounds like your favorites' appears after rating tracks", any(s["id"] == "like-favs" for s in call(B, "GET", "/api/shelves")[1]["shelves"]))
    st, fav = call(B, "GET", "/api/similar-set?ids=" + ",".join(map(str, ids[:3])))
    check("similar-to-several works", st == 200 and len(fav["tracks"]) > 0 and not set(ids[:3]) & {t["id"] for t in fav["tracks"]})

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed in {time.time() - t_start:.0f}s")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
