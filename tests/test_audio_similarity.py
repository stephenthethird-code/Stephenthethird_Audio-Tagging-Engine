"""Run: python3 -m unittest discover tests"""
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path

import numpy as np

from audio_engine import analysis_job as aj, db, features, library, similarity as sim, smart_search as ss

SR = 22050


def _write(path: Path, y: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(y, -1, 1) * 32000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


def _tone(freq, secs=6, noise=0.0, seed=0, harmonics=1):
    t = np.arange(int(SR * secs)) / SR
    y = sum(np.sin(2 * np.pi * freq * (k + 1) * t) / (k + 1) for k in range(harmonics))
    y = 0.4 * y / max(1, harmonics ** 0.5)
    return y + noise * np.random.RandomState(seed).randn(len(t))


class AudioSimilarityTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.dbfile = tmp / "t.sqlite"
        db.DB_PATH = self.dbfile
        db.init_db(self.dbfile)
        self.dir = tmp / "audio"
        # two near-identical warm tones, a bright buzzy one, and pure noise
        self.files = {"warm_a": _tone(220, harmonics=3), "warm_b": _tone(220, noise=0.002, seed=1, harmonics=3),
                      "bright": _tone(3000, harmonics=1), "noise": _tone(100, noise=0.3, seed=2)}
        conn = db.connect(self.dbfile)
        for name, y in self.files.items():
            _write(self.dir / f"{name}.wav", y)
            library.index_file(self.dir / f"{name}.wav", deep=False, conn=conn)
        conn.commit(); conn.close()
        self.ids = {r["filename"][:-4]: r["id"] for r in db.connect(self.dbfile).execute("SELECT id, filename FROM tracks")}

    def _run(self, **kw):
        job = aj.start(**kw)
        for _ in range(300):
            if job.state != "running":
                break
            time.sleep(0.1)
        self.assertNotEqual(job.state, "running")
        return job

    def test_features_shape_and_silence(self):
        r = features.extract(self.files["warm_a"].astype(np.float32))
        self.assertEqual(r["vec"].shape, (features.DIM,)); self.assertTrue(np.isfinite(r["vec"]).all())
        with self.assertRaises(ValueError):
            features.extract(np.zeros(5000, dtype=np.float32))

    def test_analysis_fills_detected_layer_and_is_resumable(self):
        j1 = self._run(limit=2)
        self.assertEqual((j1.state, j1.done), ("done", 2))
        cov = aj.coverage()
        self.assertEqual((cov["analyzed"], cov["pending"]), (2, 2))
        j2 = self._run()                                    # resumes: only the 2 remaining tracks are processed
        self.assertEqual(j2.total, 2)
        cov = aj.coverage(); self.assertEqual((cov["analyzed"], cov["pending"], cov["failed"]), (4, 0, 0))
        conn = db.connect(self.dbfile)
        self.assertTrue(all(r["camelot_key"] for r in conn.execute("SELECT camelot_key FROM tracks")))
        self.assertEqual(self._run().total, 0)              # nothing pending -> no-op

    def test_filename_bpm_beats_audio_estimate(self):
        conn = db.connect(self.dbfile)
        conn.execute("UPDATE tracks SET bpm = 99, bpm_source = 'filename' WHERE filename = 'warm_a.wav'"); conn.commit(); conn.close()
        self._run()
        r = db.connect(self.dbfile).execute("SELECT bpm, bpm_source FROM tracks WHERE filename = 'warm_a.wav'").fetchone()
        self.assertEqual((r["bpm"], r["bpm_source"]), (99, "filename"))

    def test_unreadable_recorded_missing_stays_pending(self):
        (self.dir / "warm_b.wav").write_bytes(b"not audio at all")      # corrupt -> error recorded, not retried
        (self.dir / "noise.wav").unlink()                               # unplugged drive -> pending, not an error
        job = self._run()
        self.assertEqual((job.done, job.failed, job.missing), (2, 1, 1))
        cov = aj.coverage(); self.assertEqual((cov["failed"], cov["pending"]), (1, 1))
        self.assertEqual(self._run().total, 1)                          # only the missing one is still pending

    def test_neighbours_and_similar_to(self):
        self._run()
        conn = db.connect(self.dbfile)
        top = sim.neighbours(conn, track_id=self.ids["warm_a"], k=3)
        self.assertEqual(top[0]["id"], self.ids["warm_b"])             # near-identical tone ranks first
        self.assertNotIn(self.ids["warm_a"], [h["id"] for h in top])
        res = ss.similar_to(self.ids["warm_a"])
        self.assertEqual((res["mode"], res["by"]), ("similar", "audio"))
        self.assertEqual(res["tracks"][0]["filename"], "warm_b.wav")
        self.assertIn("timbre", res["tracks"][0]["match"]["why"] + "timbre")
        only = ss.similar_to(self.ids["warm_a"], groups=["pitch"])
        self.assertEqual(only["groups"], ["pitch"])
        tags = ss.similar_to(self.ids["warm_a"], by="tags")
        self.assertEqual(tags["by"], "tags")

    def test_similar_before_analysis_falls_back_and_never_empty(self):
        res = ss.similar_to(self.ids["warm_a"])
        self.assertEqual(res["by"], "tags"); self.assertTrue(res["needs_analysis"]); self.assertGreater(len(res["tracks"]), 0)

    def test_mimic_clip_finds_the_matching_sound_and_never_empty(self):
        clip = features.extract(_tone(220, secs=3, noise=0.004, seed=9, harmonics=3).astype(np.float32))
        before = ss.sound_search(clip["vec"], clip)                    # nothing analysed yet
        self.assertTrue(before["needs_analysis"]); self.assertGreater(len(before["tracks"]), 0)
        self._run()
        res = ss.sound_search(clip["vec"], clip)
        self.assertEqual(res["by"], "clip")
        self.assertIn(res["tracks"][0]["filename"], ("warm_a.wav", "warm_b.wav"))
        self.assertGreater(res["tracks"][0]["match"]["pct"], res["tracks"][-1]["match"]["pct"])


if __name__ == "__main__":
    unittest.main()
