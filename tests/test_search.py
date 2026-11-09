"""Run: python3 -m unittest discover tests"""
import os
import tempfile
import unittest
from pathlib import Path

from audio_engine import db, search
from audio_engine.tagging import bpm_from_filename, suggest_tags


class TaggingTests(unittest.TestCase):
    def test_bpm_parsing(self):
        for name, want in {"night shift 86bpm jane.mp3": 86, "slow down prod jane 76.mp3": 76,
                           "90's sitcom.mp3": None, "08-15-23 idea.wav": None, "01 Beam - Win.mp3": None,
                           "3861 95 kalimbatones.m4a": 95}.items():
            self.assertEqual(bpm_from_filename(name), want, name)

    def test_suggest(self):
        s = suggest_tags("/x/EXPORTS/BEATS/2024/dark piano trap 140bpm.mp3", 150)
        self.assertEqual(set(s["tags"]), {"beat", "2024", "dark", "piano", "trap"})
        self.assertEqual((s["bpm"], s["sample_type"]), (140.0, "track"))


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.dbfile = Path(tempfile.mkdtemp()) / "t.sqlite"
        os.environ["AUDIO_LIBRARY_DB"] = str(self.dbfile)
        db.DB_PATH = self.dbfile
        db.init_db(self.dbfile)
        conn = db.connect(self.dbfile)
        for i, (name, dur) in enumerate([("/L/BEATS/2024/dark trap 140bpm a.mp3", 120),
                                         ("/L/BEATS/2023/chill piano 90bpm b.mp3", 100),
                                         ("/L/SAMPLES/2024/kick one shot.wav", 1)], 1):
            conn.execute("INSERT INTO tracks (path, filename, duration_sec, created_at, updated_at) VALUES (?,?,?,?,?)",
                         (name, Path(name).name, dur, "t", "t"))
        conn.commit(); conn.close()
        search.autotag_library()

    def test_filters_and_summary(self):
        r = search.search_tracks(tags=["beat"], bpm_min=100, bpm_max=150)
        self.assertEqual([t["filename"] for t in r["tracks"]], ["dark trap 140bpm a.mp3"])
        counts = {x["tag"]: x["count"] for x in r["tag_summary"]}
        self.assertEqual(counts["beat"], 1)
        self.assertEqual(search.search_tracks(tags=["beat"])["total"], 2)
        self.assertEqual(search.search_tracks(tags_exclude=["beat"])["total"], 1)
        self.assertEqual(search.search_tracks(sample_type="one-shot")["total"], 1)

    def test_collections_and_rename(self):
        self.assertEqual(search.add_to_collection("EP", [1, 2, 999]), 2)  # 999 doesn't exist: ignored
        self.assertEqual(search.search_tracks(collection="EP")["total"], 2)
        search.rename_tag("trap", "hip hop")
        self.assertEqual(search.search_tracks(tags=["hip hop"])["total"], 1)

    def test_autotag_idempotent(self):
        self.assertEqual(search.autotag_library()["tags_added"], 0)


if __name__ == "__main__":
    unittest.main()
