# audio_engine/config.py
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Override with the AUDIO_LIBRARY_DB environment variable if you want the DB somewhere else.
DB_PATH = Path(os.environ.get("AUDIO_LIBRARY_DB", PROJECT_ROOT / "db" / "library.sqlite"))

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".aiff", ".aif", ".opus", ".wma",
}

# The web app's analyzer (BPM, key, tags) that analysis.py reuses. See analysis.py.
BACKEND_MAIN = PROJECT_ROOT / "audio-web" / "backend" / "main.py"


def _load_author_words():
    """Words stripped when comparing titles (your own name / handle, e.g. 'prod jane'). Set them in db/settings.json
    {"author_words": ["jane", "janedoe"]} or the AUDIO_LIBRARY_AUTHOR_WORDS env var (comma separated)."""
    words = {w.strip().lower() for w in os.environ.get("AUDIO_LIBRARY_AUTHOR_WORDS", "").split(",") if w.strip()}
    try:
        import json
        words |= {str(w).lower() for w in json.loads((PROJECT_ROOT / "db" / "settings.json").read_text()).get("author_words", [])}
    except (OSError, ValueError):
        pass
    return words


AUTHOR_WORDS = _load_author_words()
