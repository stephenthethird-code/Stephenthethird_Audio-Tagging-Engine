"""Run: python3 -m unittest discover tests"""
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path

from audio_engine import db, librarian as lb, smart_search as ss


def _wav(path: Path, seconds: float = 0.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * int(8000 * seconds))


class LibrarianTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.root = tmp / "music"
        for rel in ["BEATS/2024/dark trap 140bpm.wav", "BEATS/2024/chill piano 90bpm.wav",
                    "SAMPLES/2023/kalimba loop 128bpm.wav", "SAMPLES/2023/kick one shot.wav"]:
            _wav(self.root / rel)
        (self.root / "notes.txt").write_text("not audio")
        self.dbfile = tmp / "t.sqlite"
        db.DB_PATH = self.dbfile
        db.init_db(self.dbfile)

    def _import(self):
        job = lb.start_import(str(self.root))
        for _ in range(100):
            if job.state in ("done", "error", "cancelled"):
                break
            time.sleep(0.1)
        self.assertEqual(job.state, "done", job.error)
        return job

    def test_scan_is_read_only_then_import(self):
        pre = lb.scan_folder(str(self.root))
        self.assertEqual((pre["total"], pre["new"], pre["by_format"]), (4, 4, {"wav": 4}))
        conn = db.connect(self.dbfile)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 0)  # preview wrote nothing
        conn.close()
        job = self._import()
        self.assertEqual((job.counts["added"], job.counts["failed"], job.done), (4, 0, 4))
        self.assertGreater(job.tag_stats["tags_added"], 0)                 # auto-tag ran as part of the import
        again = lb.scan_folder(str(self.root))
        self.assertEqual((again["new"], again["already_in_library"]), (0, 4))
        r = ss.search("trap 140")
        self.assertEqual(r["tracks"][0]["filename"], "dark trap 140bpm.wav")

    def test_batch_edit_is_non_destructive_and_undoable(self):
        self._import()
        ids = [t["id"] for t in ss.search("beat", per_page=10)["tracks"] if "beat" in t["tags"]]
        self.assertEqual(len(ids), 2)
        res = lb.apply_batch(ids, add_tags=["Moody"], remove_tags=["2024"], set_fields={"bpm": 150, "rating": 4, "key": "A minor"})
        self.assertEqual(res["changed"], 2)
        top = ss.search("moody", tags_exclude=None)["tracks"][0]
        self.assertEqual((top["bpm"], top["bpm_source"], top["camelot_key"], top["rating"]), (150.0, "manual", "8A", 4))
        self.assertNotIn("2024", top["tags"])
        conn = db.connect(self.dbfile)                                      # detected value is untouched underneath
        self.assertEqual(conn.execute("SELECT bpm FROM tracks WHERE id = ?", (top["id"],)).fetchone()[0] in (140.0, 90.0), True)
        conn.close()
        d = lb.track_detail(top["id"])
        self.assertIn(d["detected"]["bpm"], (140.0, 90.0)); self.assertEqual(d["effective"]["bpm"], 150.0)
        self.assertEqual(ss.search("", rating_min=4)["mode"], "exact")

        lb.undo_batch(res["batch_id"])
        back = [t for t in ss.search("beat", per_page=10)["tracks"] if t["id"] == top["id"]][0]
        self.assertIn(back["bpm"], (140.0, 90.0)); self.assertIn("2024", back["tags"]); self.assertNotIn("moody", back["tags"])
        self.assertIsNone(back["rating"])
        with self.assertRaises(ValueError):
            lb.undo_batch(res["batch_id"])                                   # can't undo twice

    def test_collection_add_is_undoable(self):
        self._import()
        ids = [t["id"] for t in ss.search("", per_page=3)["tracks"]]
        res = lb.apply_batch(ids, collection="Reel")
        self.assertEqual(ss.search("", collection="Reel")["exact_count"], 3)
        lb.undo_batch(res["batch_id"])
        r = ss.search("", collection="Reel")                                 # emptied -> falls back to whole library, never dead-ends
        self.assertTrue(any("empty" in n for n in r["notes"]))

    def test_validation_and_never_empty_after_edits(self):
        self._import()
        for bad in [{"bpm": 5}, {"key": "banana"}, {"sample_type": "huge"}, {"rating": 9}, {"title": "x"}]:
            with self.assertRaises(ValueError, msg=str(bad)):
                lb.apply_batch([1], set_fields=bad)
        with self.assertRaises(ValueError):
            lb.apply_batch([], add_tags=["x"])
        lb.delete_tag("beat")
        self.assertGreater(len(ss.search("beat")["tracks"]), 0)               # tag now gone, search still answers

    def test_rename_tag_and_saved_searches(self):
        self._import()
        out = lb.rename_tag_logged("trap", "hip hop")
        self.assertEqual(ss.search("", tags=["hip hop"])["exact_count"], 1)
        lb.undo_batch(out["batch_id"])
        self.assertEqual(ss.search("", tags=["trap"])["exact_count"], 1)
        sid = lb.save_search("Slow beats", {"q": "beat", "bpm_max": 100})
        lb.save_search("slow beats", {"q": "beat", "bpm_max": 95})            # same name (case-insens.) updates
        saved = lb.list_saved_searches()
        self.assertEqual((len(saved), saved[0]["query"]["bpm_max"]), (1, 95))
        lb.delete_saved_search(sid)
        self.assertEqual(lb.list_saved_searches(), [])

    def test_folder_picker(self):
        d = lb.list_dirs(str(self.root))
        self.assertEqual((d["dirs"], d["audio_here"]), (["BEATS", "SAMPLES"], 0))
        with self.assertRaises(NotADirectoryError):
            lb.list_dirs(str(self.root / "nope"))


if __name__ == "__main__":
    unittest.main()
