"""Run: python3 -m unittest discover tests"""
import os
import tempfile
import unittest
from pathlib import Path

from audio_engine import db, search, smart_search as ss
from audio_engine.keys import to_camelot, compatibility


class SmartSearchTests(unittest.TestCase):
    def setUp(self):
        self.dbfile = Path(tempfile.mkdtemp()) / "t.sqlite"
        db.DB_PATH = self.dbfile
        db.init_db(self.dbfile)
        conn = db.connect(self.dbfile)
        for name, dur in [("/L/BEATS/2024/dark trap 140bpm a.mp3", 120), ("/L/BEATS/2023/chill piano 90bpm b.mp3", 100),
                          ("/L/SAMPLES/2024/kalimba loop 128bpm.wav", 12), ("/L/SAMPLES/2024/kick one shot.wav", 1)]:
            conn.execute("INSERT INTO tracks (path, filename, duration_sec, created_at, updated_at) VALUES (?,?,?,?,?)",
                         (name, Path(name).name, dur, "t", "t"))
        conn.commit(); conn.close()
        search.autotag_library()

    def test_never_empty(self):
        for q in ["", "zzzzqqq", "xylophone 8A 999bpm", "dark piano 140", "!!!", "   ", "a", "trap 2019 loop 300"]:
            r = ss.search(q)
            self.assertGreater(len(r["tracks"]), 0, q)
            self.assertIn(r["mode"], {"discover", "exact", "closest"}, q)

    def test_exact_first_and_typos(self):
        r = ss.search("trapp 140")
        self.assertEqual(r["tracks"][0]["filename"], "dark trap 140bpm a.mp3")
        self.assertEqual(r["corrections"], [{"from": "trapp", "to": "trap"}])
        self.assertEqual(ss.search("kalimba loop")["mode"], "exact")

    def test_explains_unmatched_words(self):
        r = ss.search("zzzzqqq")
        self.assertTrue(any("zzzzqqq" in n for n in r["notes"]))

    def test_excluding_everything_is_ignored(self):
        r = ss.search("", tags_exclude=["beat", "sample"])
        self.assertGreater(len(r["tracks"]), 0)
        self.assertTrue(any("ignored" in n for n in r["notes"]))

    def test_autocomplete_and_similar(self):
        for p in ["", "tr", "qqqq", "14", "8a"]:
            self.assertGreater(len(ss.autocomplete(p)["items"]), 0, p)
        tid = ss.search("dark trap")["tracks"][0]["id"]
        sim = ss.similar_to(tid)
        self.assertNotIn(tid, [t["id"] for t in sim["tracks"]])
        self.assertEqual(ss.similar_to(99999)["mode"], "discover")

    def test_keys(self):
        self.assertEqual((to_camelot("A minor"), to_camelot("C"), to_camelot("c# minor"), to_camelot("8a"), to_camelot("hello")),
                         ("8A", "8B", "12A", "8A", None))
        self.assertEqual((compatibility("8A", "8A"), compatibility("8A", "9A"), compatibility("8A", "8B"), compatibility("8A", "3B")),
                         (1.0, 0.6, 0.6, 0.0))


if __name__ == "__main__":
    unittest.main()
