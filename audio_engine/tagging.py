# audio_engine/tagging.py
"""
Auto-tagging: turn what a file name / folder path / duration already say into searchable tags.

Each track gets flat tags plus BPM, key, type (loop / one-shot / track) and genre, the way sample browsers describe
sounds; here they are derived locally from file names, folders and duration. The vocabularies below are a starting
point - edit them freely.
"""
import re
from pathlib import Path
from typing import Dict, List, Optional

from .library import normalize_tag

INSTRUMENTS = {
    "808", "bass", "guitar", "piano", "keys", "rhodes", "organ", "strings", "violin", "cello", "flute",
    "kalimba", "harp", "bells", "synth", "pluck", "pad", "lead", "drums", "drum", "hats", "snare", "kick",
    "vocal", "vox", "choir", "sax", "trumpet", "brass", "horns", "marimba", "sitar", "banjo",
}
GENRES = {
    "trap", "rnb", "hip hop", "pop", "jazz", "house", "drill", "afrobeats", "reggaeton", "lofi", "rock",
    "soul", "funk", "techno", "ambient", "dancehall", "latin", "country", "edm",
}
MOODS = {
    "dark", "moody", "chill", "sad", "happy", "dreamy", "aggressive", "bouncy", "melodic", "ethereal",
    "uplifting", "mellow", "hard", "smooth", "energetic",
}
# folder name -> tag. Matched case-insensitively against every folder in the path.
CATEGORY_FOLDERS = {"beats": "beat", "samples": "sample", "all records ever": "record", "records": "record",
                    "demos": "demo", "stems": "stem", "loops": "loop", "one shots": "one-shot",
                    "oneshots": "one-shot", "vox": "vocal"}

_VOCAB = {normalize_tag(w) for w in INSTRUMENTS | GENRES | MOODS}
_BPM_EXPLICIT = re.compile(r"(?<!\d)(\d{2,3})\s*bpm\b", re.I)
_BARE_NUMBER = re.compile(r"(?<![\d.'])(\d{2,3})(?![\d'.]|\s*(?:'s|s\b))")
_YEAR_DIR = re.compile(r"\b(20\d\d)\b")
# "x", "feat", "ft" before a name = a collaborator. Names stay as free text, so they are not auto-tagged.
BPM_RANGE = (60, 200)


def bpm_from_filename(filename: str) -> Optional[float]:
    """'night shift 86bpm' -> 86. Falls back to a lone bare number in range ('slow down prod jane 76' -> 76).
    Ignores dates (08-15-23), track numbers (01) and decades (90's)."""
    stem = Path(filename).stem
    m = _BPM_EXPLICIT.search(stem)
    if m:
        v = int(m.group(1))
        return float(v) if 30 <= v <= 300 else None
    stem_no_dates = re.sub(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}", " ", stem)
    cands = [int(x.group(1)) for x in _BARE_NUMBER.finditer(stem_no_dates)
             if BPM_RANGE[0] <= int(x.group(1)) <= BPM_RANGE[1]]
    return float(cands[-1]) if cands else None  # tempo is usually written last, after the title


def classify_sample_type(duration_sec: Optional[float], filename: str = "", tags=()) -> Optional[str]:
    """'one-shot' (<4s), 'loop' (named loop, or short + tempo-labelled), else 'track' (a full song/beat)."""
    low = filename.lower()
    if "one shot" in low or "oneshot" in low or "one-shot" in low or "one-shot" in tags:
        return "one-shot"
    if re.search(r"\bloop\b", low) or "loop" in tags:
        return "loop"
    if duration_sec is None:
        return None
    if duration_sec < 4:
        return "one-shot"
    if duration_sec <= 32:
        return "loop"
    return "track"


def suggest_tags(path: str, duration_sec: Optional[float] = None) -> Dict[str, object]:
    """Returns {'tags': [...], 'bpm': float|None, 'sample_type': str|None}. Pure function: no DB access."""
    p = Path(path)
    tags: List[str] = []

    def add(t: str):
        t = normalize_tag(t)
        if t and t not in tags:
            tags.append(t)

    for folder in p.parts[:-1]:
        f = folder.strip().lower()
        if f in CATEGORY_FOLDERS:
            add(CATEGORY_FOLDERS[f])
        y = _YEAR_DIR.search(f)
        if y:
            add(y.group(1))

    # vocabulary words in the file name (single words and the two-word 'hip hop')
    text = normalize_tag(re.sub(r"[^\w&' ]+", " ", p.stem))
    padded = f" {text} "
    for term in sorted(_VOCAB):
        if f" {term} " in padded:
            add(term)

    bpm = bpm_from_filename(p.name)
    return {"tags": tags, "bpm": bpm, "sample_type": classify_sample_type(duration_sec, p.name, tags)}


_KIND_SETS = {"instrument": {normalize_tag(w) for w in INSTRUMENTS}, "genre": {normalize_tag(w) for w in GENRES},
              "mood": {normalize_tag(w) for w in MOODS},
              "type": set(CATEGORY_FOLDERS.values()) | {"loop", "one-shot"}}


def tag_kind(tag: str) -> str:
    """Facet group for a tag: year / instrument / genre / mood / type / other (used to group the tag sidebar)."""
    if re.fullmatch(r"(19|20)\d\d", tag):
        return "year"
    for kind, members in _KIND_SETS.items():
        if tag in members:
            return kind
    return "other"
