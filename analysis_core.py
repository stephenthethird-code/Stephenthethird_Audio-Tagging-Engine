# analysis_core.py

import os
import math
import soundfile as sf
import librosa
from mutagen import File as MutagenFile

PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F",
                 "F#", "G", "G#", "A", "A#", "B"]


def read_metadata(path: str) -> dict:
    """
    Return basic metadata: duration, samplerate, channels,
    plus tags from the file (title, artist, album, etc.).
    """
    info = {
        "path": path,
        "filename": os.path.basename(path),
        "duration_sec": None,
        "samplerate": None,
        "channels": None,
        "tags": {},
    }

    # Audio properties via soundfile
    try:
        with sf.SoundFile(path) as f:
            info["samplerate"] = f.samplerate
            info["channels"] = f.channels
            info["duration_sec"] = len(f) / float(f.samplerate)
    except Exception:
        # Non-fatal: some formats may not be supported by soundfile
        pass

    # Tags via mutagen
    try:
        m = MutagenFile(path, easy=True)
        if m is not None and m.tags:
            for k, v in m.tags.items():
                if isinstance(v, list):
                    info["tags"][k] = ", ".join(str(x) for x in v)
                else:
                    info["tags"][k] = str(v)
    except Exception:
        pass

    return info


def detect_bpm_and_key(path: str) -> dict:
    """
    Use librosa to estimate tempo (BPM) and key (very rough!).
    Works best for reasonably clean musical material.
    """
    result = {
        "bpm": None,
        "key": None,
    }

    try:
        # sr=None -> keep native samplerate; mono=True is fine for BPM/key
        y, sr = librosa.load(path, sr=None, mono=True)

        # Estimate tempo
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        if tempo > 0:
            result["bpm"] = round(float(tempo), 1)

        # Estimate key via chroma
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = chroma.mean(axis=1)
        idx = int(chroma_mean.argmax())
        result["key"] = PITCH_CLASSES[idx]

    except Exception:
        # Don't crash UI on analysis issues
        pass

    return result
