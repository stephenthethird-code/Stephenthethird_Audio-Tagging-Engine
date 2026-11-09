# audio_engine/keys.py
"""Musical key helpers: 'C# minor' / 'Am' / '8A' -> Camelot code, and Camelot neighbours (harmonic mixing)."""
import re
from typing import Optional, Set

_FLAT_TO_SHARP = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}
# same wheel as audio-web/backend/main.py (B = major, A = minor)
_MAJOR = {"B": 1, "F#": 2, "C#": 3, "G#": 4, "D#": 5, "A#": 6, "F": 7, "C": 8, "G": 9, "D": 10, "A": 11, "E": 12}
_MINOR = {"G#": 1, "D#": 2, "A#": 3, "F": 4, "C": 5, "G": 6, "D": 7, "A": 8, "E": 9, "B": 10, "F#": 11, "C#": 12}

_CAMELOT = re.compile(r"^(1[0-2]|[1-9])\s*([ab])$", re.I)
_NAME = re.compile(r"^([a-g])\s*(#|b|sharp|flat)?\s*(major|minor|maj|min|m)?$", re.I)


def to_camelot(text: Optional[str]) -> Optional[str]:
    """'8a' -> '8A', 'C# minor' -> '12A', 'A min' -> '8A', 'F' -> '7B' (a bare note means major). None if not a key."""
    if not text:
        return None
    t = text.strip()
    m = _CAMELOT.match(t)
    if m:
        return f"{int(m.group(1))}{m.group(2).upper()}"
    m = _NAME.match(t)
    if not m:
        return None
    note = m.group(1).upper() + {"#": "#", "sharp": "#", "b": "b", "flat": "b"}.get((m.group(2) or "").lower(), "")
    if note.endswith("b"):
        note = _FLAT_TO_SHARP.get(note.upper(), note)
    quality = (m.group(3) or "major").lower()
    minor = quality in ("minor", "min", "m") and m.group(3) is not None
    table = _MINOR if minor else _MAJOR
    # an uppercase-less bare 'b' would be ambiguous with a flat sign; the regex only reads it as flat after a note
    return f"{table[note]}{'A' if minor else 'B'}" if note in table else None


def compatibility(a: Optional[str], b: Optional[str]) -> float:
    """1.0 same key, 0.6 harmonic neighbour (+-1 on the wheel, or relative major/minor), else 0."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    na, la, nb, lb = int(a[:-1]), a[-1], int(b[:-1]), b[-1]
    if la == lb and (na - nb) % 12 in (1, 11):
        return 0.6
    if na == nb and la != lb:
        return 0.6
    return 0.0


def neighbours(code: str) -> Set[str]:
    n, l = int(code[:-1]), code[-1]
    return {code, f"{(n % 12) + 1}{l}", f"{((n - 2) % 12) + 1}{l}", f"{n}{'B' if l == 'A' else 'A'}"}


_PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler profiles (same ones the converter's analyzer uses)
_KS_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KS_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def detect_key(chroma_mean):
    """12-bin average chroma -> (camelot_code, 'C# minor', confidence 0..1). Confidence is the best profile correlation."""
    import numpy as np
    c = np.asarray(chroma_mean, dtype=float)
    if c.shape != (12,) or c.std() < 1e-9:
        return None, None, 0.0
    best = (-2.0, None)
    for i in range(12):
        rolled = np.roll(c, -i)
        for mode, prof in (("major", _KS_MAJOR), ("minor", _KS_MINOR)):
            r = float(np.corrcoef(rolled, prof)[0, 1])
            if r > best[0]:
                best = (r, f"{_PITCHES[i]} {mode}")
    return to_camelot(best[1]), best[1], max(0.0, best[0])
