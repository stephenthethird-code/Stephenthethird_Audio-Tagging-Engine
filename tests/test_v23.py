"""Version groups, send tracker, moods, genre suggestions, shelves. Run: python3 -m unittest discover tests"""
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from audio_engine import (analysis_job as aj, db, genres, librarian as lb, library, moods, sends, shelves,
                          smart_search as ss, versions)
from tests.test_audio_similarity import _write, _tone


versions.AUTHOR_WORDS.add("jane")          # tests name their files 'prod jane'


def _fresh():
    tmp = Path(tempfile.mkdtemp()); dbfile = tmp / "t.sqlite"
    db.DB_PATH = dbfile; db.init_db(dbfile)
    ss.invalidate()
    return tmp, dbfile


def _add(dirp: Path, dbfile, name, y):
    _write(dirp / name, y)
    conn = db.connect(dbfile); library.index_file(dirp / name, deep=False, conn=conn); conn.commit(); conn.close()


def _analyze():
    job = aj.start()
    for _ in range(600):
        if job.state != "running": break
        time.sleep(0.1)
    assert job.state == "done", job.public()


def _id(name):
    c = db.connect(); r = c.execute("SELECT id FROM tracks WHERE filename = ?", (name,)).fetchone(); c.close(); return r[0]


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.dbfile = _fresh()
        a = _tone(220, secs=9, harmonics=3)
        _add(self.tmp, self.dbfile, "midnight drive prod jane 115.wav", a)
        _add(self.tmp, self.dbfile, "midnight drive prod jane 115 FINAL v2.wav", _tone(330, secs=9, harmonics=2))   # same title, different audio
        _add(self.tmp, self.dbfile, "zeta mix copy.wav", a)                                                # same audio, different title
        _add(self.tmp, self.dbfile, "completely different song.wav", _tone(3000, secs=9))
        _add(self.tmp, self.dbfile, "Audio 5_1.wav", _tone(500, secs=9)); _add(self.tmp, self.dbfile, "Audio 7_1.wav", _tone(1700, secs=9))
        _analyze()

    def test_stem_rules(self):
        self.assertEqual(versions.stem("night shift prod jane 72 FINAL V2.mp3"), "night shift")
        self.assertFalse(versions.valid_stem(versions.stem("Audio 5_1.wav")))
        self.assertFalse(versions.valid_stem("demo loop"))                 # only generic words -> never a title
        self.assertFalse(versions.valid_stem("jane x"))                     # too short: a bare name is not a title
        self.assertTrue(versions.valid_stem("midnight drive"))

    def test_rebuild_stack_primary_and_locks(self):
        res = versions.rebuild()
        self.assertEqual(res["groups"], 1)                                   # midnight drive x2 + zeta (identical audio) merge via union; Audio 5/7 don't
        g = versions.list_groups()["groups"][0]
        names = {m["filename"] for m in g["members"]}
        self.assertEqual(names, {"midnight drive prod jane 115.wav", "midnight drive prod jane 115 FINAL v2.wav", "zeta mix copy.wav"})
        self.assertTrue([m for m in g["members"] if m["is_primary"]][0]["filename"].lower().find("final") >= 0)   # 'final' wins
        stacked = ss.search("", per_page=50)
        self.assertEqual(len([t for t in stacked["tracks"] if t["group"]]), 1)         # the group shows once...
        self.assertEqual(stacked["stacked"], 2)                                         # ...hiding two others
        self.assertEqual(len(ss.search("", per_page=50, stack=False)["tracks"]), 6)
        # similar-to never lists the track's own versions
        sim = ss.similar_to(_id("zeta mix copy.wav"))
        self.assertFalse({"midnight drive prod jane 115.wav", "midnight drive prod jane 115 FINAL v2.wav"} & {t["filename"] for t in sim["tracks"]})
        # user decisions survive a rescan
        other = _id("zeta mix copy.wav")
        versions.set_primary(g["id"], other); versions.rebuild()
        self.assertEqual(versions.group_detail(versions.group_of(other))["primary_id"], other)
        versions.ungroup(_id("midnight drive prod jane 115.wav")); versions.rebuild()
        self.assertIsNone(versions.group_of(_id("midnight drive prod jane 115.wav")))

    def test_short_similar_sounds_are_not_duplicates(self):
        t2, d2 = _fresh()
        for i in range(4):                                 # four near-identical 1 s one-shots from one 'pack'
            _add(t2, d2, f"kick hit {i} soft.wav", _tone(60, secs=1, noise=0.0005, seed=i))
        _analyze()
        self.assertEqual(versions.rebuild()["groups"], 0)

    def test_merge(self):
        gid = versions.merge([_id("Audio 5_1.wav"), _id("Audio 7_1.wav"), _id("completely different song.wav")])
        self.assertEqual(len(versions.group_detail(gid)["members"]), 3)
        with self.assertRaises(ValueError):
            versions.merge([1])


class SendTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.dbfile = _fresh()
        _add(self.tmp, self.dbfile, "dark trap beat 140bpm v1.wav", _tone(220, harmonics=2))
        _add(self.tmp, self.dbfile, "dark trap beat 140bpm FINAL.wav", _tone(220, harmonics=2, noise=0.001, seed=3))
        _add(self.tmp, self.dbfile, "soft rnb idea 90bpm.wav", _tone(440))
        library.tag_track(_id("dark trap beat 140bpm v1.wav"), ["trap"]); library.tag_track(_id("dark trap beat 140bpm FINAL.wav"), ["trap"])
        library.tag_track(_id("soft rnb idea 90bpm.wav"), ["rnb"])
        versions.merge([_id("dark trap beat 140bpm v1.wav"), _id("dark trap beat 140bpm FINAL.wav")])
        self.trap_guy = sends.save_person({"name": "Trap Guy", "tier": 2, "tags": "role:artist, trap"})
        self.rnb_gal = sends.save_person({"name": "RnB Gal", "tier": 3, "tags": "rnb"})
        self.nobody = sends.save_person({"name": "Banned", "tier": 5, "tags": "trap"})

    def test_ranking_and_version_awareness(self):
        v1 = _id("dark trap beat 140bpm v1.wav"); final = _id("dark trap beat 140bpm FINAL.wav")
        rec = sends.suggest_recipients(v1)["recipients"]
        self.assertEqual(rec[0]["name"], "Trap Guy"); self.assertIn("likes trap", rec[0]["reasons"][0])
        self.assertNotIn("Banned", [r["name"] for r in rec])                      # blacklist never suggested
        out = sends.record_sends([v1], self.trap_guy, "pitched")
        self.assertEqual(len(out["send_ids"]), 1)
        self.assertEqual(sends.record_sends([v1], self.trap_guy)["skipped"], [v1])  # no double logging
        # he heard v1, so he is not suggested FINAL either
        self.assertNotIn("Trap Guy", [r["name"] for r in sends.suggest_recipients(final)["recipients"]])
        self.assertTrue(sends.sends_for_track(final)[0]["via_version"])
        self.assertEqual(ss.search("", sent="never", per_page=20)["tracks"][0]["filename"], "soft rnb idea 90bpm.wav")
        # what to send Trap Guy next: not the trap stack he heard
        nxt = sends.suggest_tracks_for_person(self.trap_guy)["tracks"]
        self.assertNotIn("dark trap beat 140bpm FINAL.wav", [t["filename"] for t in nxt])
        self.assertGreater(len(nxt), 0)
        self.assertEqual(sends.delete_sends(out["send_ids"]), 1)
        self.assertIn("Trap Guy", [r["name"] for r in sends.suggest_recipients(final)["recipients"]])

    def test_people_crud_and_never_empty(self):
        self.assertEqual(sends.list_people("trap")["total"], 2)
        sends.save_person({"id": self.rnb_gal, "name": "RnB Gal Renamed", "tier": 1, "tags": ["rnb", "soul"]})
        self.assertEqual(sends.list_people("renamed")["people"][0]["tier"], 1)
        with self.assertRaises(ValueError): sends.save_person({"name": " "})
        with self.assertRaises(ValueError): sends.save_person({"name": "x", "tier": 9})
        res = sends.suggest_recipients(_id("soft rnb idea 90bpm.wav"))                 # only an rnb contact matches
        self.assertEqual(res["recipients"][0]["name"], "RnB Gal Renamed")


class MoodGenreTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.dbfile = _fresh()
        rs = np.random.RandomState(0)
        for i in range(12):   # warm low drones ("house-ish") and bright high tones ("trap-ish")
            _add(self.tmp, self.dbfile, f"low drone {i:02d}.wav", _tone(100 + 15 * i, secs=5, harmonics=3, noise=0.003, seed=i))
            _add(self.tmp, self.dbfile, f"high ping {i:02d}.wav", _tone(2500 + 120 * i, secs=5, noise=0.01 + 0.01 * (i % 3), seed=50 + i))
        _analyze()

    def test_moods_auto_tag_and_rejection(self):
        r = moods.apply()                                   # (the analysis job already applied moods automatically when it finished)
        self.assertTrue(r["applied"]); self.assertEqual(r["added"], 0)
        c = db.connect()
        auto = c.execute("SELECT track_id, tag FROM auto_tags WHERE source = 'audio-mood' AND rejected = 0").fetchall()
        self.assertTrue(auto)
        tid, tag = auto[0]
        row = ss.search("", tags=[tag], per_page=50)["tracks"]
        self.assertIn(tag, [a for t in row if t["id"] == tid for a in t["auto_tags"]])        # marked as auto in results
        lb.apply_batch([tid], remove_tags=[tag])                                                # user removes it ...
        moods.apply()                                                                           # ... and a re-run must not bring it back
        self.assertNotIn(tag, {r[0] for r in db.connect().execute("SELECT tag FROM track_tags WHERE track_id = ?", (tid,))})
        lb.undo_batch(c.execute("SELECT batch_id FROM edit_batches ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertIn(tag, {r[0] for r in db.connect().execute("SELECT tag FROM track_tags WHERE track_id = ?", (tid,))})   # undo restores

    def test_moods_need_enough_tracks(self):
        t2, d2 = _fresh()
        _add(t2, d2, "only one.wav", _tone(220)); _analyze()
        self.assertFalse(moods.apply()["applied"])

    def test_genre_suggestions_learn_from_examples(self):
        self.assertIn("Label a few", genres.suggestions()["reason"])                            # nothing labeled -> asks for examples
        for i in range(4):
            library.tag_track(_id(f"low drone {i:02d}.wav"), ["house"]); library.tag_track(_id(f"high ping {i:02d}.wav"), ["trap"])
        ov = genres.overview(); self.assertEqual({g["genre"]: g["count"] for g in ov["genres"]}, {"house": 4, "trap": 4})
        s = genres.suggestions(min_conf=0.4)
        self.assertGreater(len(s["suggestions"]), 8)
        right = [x for x in s["suggestions"] if (x["genre"] == "house") == x["filename"].startswith("low")]
        self.assertGreaterEqual(len(right), 0.9 * len(s["suggestions"]))                         # synthetic classes are easy; must separate
        pick = s["suggestions"][0]
        out = genres.accept([{"track_id": pick["track_id"], "genre": pick["genre"]}])
        self.assertEqual(out["accepted"], 1)
        self.assertIn(pick["genre"], [t for r in ss.search("", tags=[pick["genre"]], per_page=50)["tracks"] if r["id"] == pick["track_id"] for t in r["auto_tags"]])
        lb.undo_batch(out["batch_ids"][0])
        other = genres.suggestions(min_conf=0.4)["suggestions"][0]
        genres.reject([{"track_id": other["track_id"], "genre": other["genre"]}])
        self.assertNotIn((other["track_id"], other["genre"]), {(x["track_id"], x["genre"]) for x in genres.suggestions(min_conf=0.4)["suggestions"]})

    def test_shelves(self):
        for i in range(3):
            lb.apply_batch([_id(f"low drone {i:02d}.wav")], set_fields={"rating": 5})
        moods.apply()
        built = shelves.build(seed=1)
        ids = [s["id"] for s in built["shelves"]]
        self.assertIn("never-sent", ids); self.assertIn("like-favs", ids); self.assertIn("recent", ids)
        favs = shelves.favorites_ids(db.connect())
        res = shelves.similar_to_set(favs)
        self.assertEqual(res["by"], "audio"); self.assertTrue(res["tracks"])
        self.assertTrue(all(t["id"] not in favs for t in res["tracks"]))
        self.assertNotIn("empty", shelves.build())


if __name__ == "__main__":
    unittest.main()
