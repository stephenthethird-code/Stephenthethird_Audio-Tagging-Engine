# audio_engine/analysis.py
"""
analyze_file(path) is the ONE place the engine reads a file's metadata and (optionally) detects tempo/key.

  * metadata (duration, tags) via mutagen - fast, no decoding.
  * deep=True also runs the sound analysis in features.py (tempo + key from the audio).
The library's normal path is the quick metadata pass at import plus the background analysis job (analysis_job.py);
deep=True here is only used by the legacy command line indexer.
"""
import logging
import os
from typing import Any, Dict

log = logging.getLogger(__name__)


def _basic_metadata(path: str) -> Dict[str, Any]:
    """Duration + tags straight from mutagen. Never raises: an unreadable file just yields empty metadata."""
    meta = {"duration_sec": None, "tags": {}}
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            length = getattr(audio.info, "length", None)
            meta["duration_sec"] = round(float(length), 3) if length else None
        easy = MutagenFile(path, easy=True)
        if easy is not None and easy.tags:
            for name, value in easy.tags.items():
                if isinstance(value, (list, tuple)):
                    value = ", ".join(str(v) for v in value)
                meta["tags"][str(name).lower()] = str(value)
    except Exception:
        pass
    return meta


def analyze_file(path: str, deep: bool = True) -> Dict[str, Any]:
    """Returns: bpm, key, camelot_key, duration_sec, format, artist, title, album, genre, tags (dict)."""
    result = {"bpm": None, "key": None, "camelot_key": None, "duration_sec": None,
              "format": os.path.splitext(path)[1].lower().lstrip(".") or None,
              "artist": None, "title": None, "album": None, "genre": None, "tags": {}}
    meta = _basic_metadata(path)
    if deep:
        try:
            from . import features
            found = features.analyze_path(path)
            result["bpm"], result["key"], result["camelot_key"] = found["bpm"], found["key_name"], found["key"]
        except Exception as exc:                      # e.g. ffmpeg can't decode the file
            log.warning("Sound analysis failed for %s: %s", path, exc)
    result["duration_sec"] = meta.get("duration_sec")
    result["tags"] = meta.get("tags") or {}
    for field in ("artist", "title", "album", "genre"):
        result[field] = result["tags"].get(field)
    return result
