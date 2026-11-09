# audio_engine/features.py
"""
Sound fingerprint for a track: one fixed-length vector built from the first minute of audio.

Groups (each is compared separately, so the UI can weight them like Sononym's similarity sliders):
  timbre    20 MFCC means + 20 stds        "what it's made of": instrument/texture
  spectrum  centroid, rolloff, bandwidth, flatness, zero-crossings, 7 contrast bands     brightness / noisiness
  pitch     12-bin average chroma          which notes/key dominate
  energy    loudness mean/std, dynamic range, onset strength                              punch / density
  rhythm    tempo as a circle (so 70 and 140 bpm are neighbours) + pulse strength
This is classic DSP (librosa), not a learned model: good at "similar tone/energy/tempo/key", not at semantics.
"""
import subprocess
import warnings
from typing import Any, Dict

import numpy as np

from .keys import detect_key

VERSION = 1
SR = 22050
SECONDS = 60
GROUPS = [("timbre", 40), ("spectrum", 12), ("pitch", 12), ("energy", 4), ("rhythm", 3)]
DIM = sum(n for _, n in GROUPS)
SLICES = {}
_o = 0
for _name, _n in GROUPS:
    SLICES[_name] = slice(_o, _o + _n)
    _o += _n


def decode(path: str, seconds: int = SECONDS, sr: int = SR) -> np.ndarray:
    """Any audio file -> mono float32 at 22.05 kHz via ffmpeg (much faster and more format-proof than librosa's loader)."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-t", str(seconds), "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                          capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError((proc.stderr or b"decode failed").decode("utf8", "replace").strip()[:200])
    return np.frombuffer(proc.stdout, dtype=np.float32)


def extract(y: np.ndarray, sr: int = SR) -> Dict[str, Any]:
    """Waveform -> {'vec': float32[DIM], 'bpm': float|None, 'key': camelot|None, 'key_conf': float, 'seconds': float}."""
    import librosa
    warnings.filterwarnings("ignore", message="Trying to estimate tuning")   # harmless on synthetic / very sparse audio
    y = np.nan_to_num(np.asarray(y, dtype=np.float32))
    seconds = len(y) / sr
    if len(y) == 0 or float(np.max(np.abs(y))) < 1e-4:
        raise ValueError("silent or empty audio")
    if len(y) < sr:                      # one-shots can be a few hundred ms; give the STFT something to chew on
        y = np.pad(y, (0, sr - len(y)))
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    P = S ** 2
    mel_db = librosa.power_to_db(librosa.feature.melspectrogram(S=P, sr=sr))
    mf = librosa.feature.mfcc(S=mel_db, n_mfcc=20)
    timbre = np.concatenate([mf.mean(1), mf.std(1)])

    cent = librosa.feature.spectral_centroid(S=S, sr=sr).mean()
    roll = librosa.feature.spectral_rolloff(S=S, sr=sr).mean()
    bw = librosa.feature.spectral_bandwidth(S=S, sr=sr).mean()
    flat = librosa.feature.spectral_flatness(S=S).mean()
    zcr = librosa.feature.zero_crossing_rate(y).mean()
    contrast = librosa.feature.spectral_contrast(S=S, sr=sr).mean(1)
    spectrum = np.concatenate([[np.log1p(cent), np.log1p(roll), np.log1p(bw), flat, zcr], contrast])

    chroma = librosa.feature.chroma_stft(S=P, sr=sr).mean(1)
    chroma = chroma / (chroma.sum() + 1e-9)

    db = librosa.amplitude_to_db(librosa.feature.rms(S=S)[0] + 1e-6)
    onset = librosa.onset.onset_strength(S=mel_db, sr=sr)
    energy = np.array([db.mean(), db.std(), np.percentile(db, 95) - np.percentile(db, 10), onset.mean()])

    tempo = 0.0
    if seconds >= 3:
        tempo = float(np.atleast_1d(librosa.beat.beat_track(onset_envelope=onset, sr=sr)[0])[0])
    pulse = float(onset.std() / (onset.mean() + 1e-6))
    if tempo > 0:
        ang = 2 * np.pi * np.log2(tempo / 60.0)
        rhythm = np.array([np.cos(ang), np.sin(ang), pulse])
    else:
        rhythm = np.array([0.0, 0.0, pulse])

    key, key_name, conf = detect_key(chroma)
    vec = np.concatenate([timbre, spectrum, chroma, energy, rhythm]).astype(np.float32)
    assert vec.shape == (DIM,), vec.shape
    return {"vec": np.nan_to_num(vec), "bpm": round(tempo, 1) if tempo > 0 else None, "key": key, "key_name": key_name, "key_conf": round(conf, 3),
            "seconds": round(seconds, 2)}


def analyze_path(path: str) -> Dict[str, Any]:
    return extract(decode(path))
