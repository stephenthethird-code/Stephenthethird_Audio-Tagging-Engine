# audio_engine/db.py
"""SQLite connection + schema. Safe to run repeatedly: python3 -m audio_engine.db"""
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from .config import DB_PATH

SCHEMA_VERSION = 6  # ...; 5 audio features; 6 version groups, auto tags (moods/genre suggestions)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT NOT NULL UNIQUE,      -- absolute path
    filename     TEXT NOT NULL,
    bpm          REAL,
    key          TEXT,                      -- e.g. 'C# minor'
    camelot_key  TEXT,                      -- e.g. '12A'
    duration_sec REAL,
    format       TEXT,                      -- file extension without the dot
    artist       TEXT,
    title        TEXT,
    album        TEXT,
    genre        TEXT,
    file_size    INTEGER,                   -- size + mtime let re-scans skip unchanged files
    file_mtime   REAL,
    analyzed_at  TEXT,                      -- set once BPM/key analysis has run
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracks_bpm    ON tracks(bpm);
CREATE INDEX IF NOT EXISTS idx_tracks_camelot ON tracks(camelot_key);

CREATE TABLE IF NOT EXISTS people (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_key  TEXT NOT NULL UNIQUE,       -- lowercase email if there is one, else lowercase name
    name        TEXT NOT NULL,
    email       TEXT,
    tags_json   TEXT NOT NULL DEFAULT '[]', -- JSON list of lowercase tags
    role        TEXT,
    tier        INTEGER,                    -- 1 (inner circle) .. 5 (blacklist)
    location    TEXT,
    company     TEXT,
    notes       TEXT,
    status      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS track_tags (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    PRIMARY KEY (track_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_track_tags_tag ON track_tags(tag);

-- parent = the beat / demo / song that uses something; child = the sample it uses.
CREATE TABLE IF NOT EXISTS track_links (
    parent_id     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    child_id      INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'uses_sample',
    created_at    TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id, relation_type),
    CHECK (parent_id != child_id)
);
CREATE INDEX IF NOT EXISTS idx_track_links_child ON track_links(child_id);

-- who has been sent which track (suggestions skip people who already got it)
CREATE TABLE IF NOT EXISTS sends (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id  INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    sent_at   TEXT NOT NULL,
    note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sends_track  ON sends(track_id);
CREATE INDEX IF NOT EXISTS idx_sends_person ON sends(person_id);

-- "Collections": named, user-curated groups of tracks (a track can be in many)
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_tracks (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    track_id      INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    added_at      TEXT NOT NULL,
    PRIMARY KEY (collection_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_collection_tracks_track ON collection_tracks(track_id);

-- NON-DESTRUCTIVE USER LAYER. `tracks` holds what was detected (filename / analysis); this table holds what the
-- user decided. Effective value = user value if set, else detected. Audio files themselves are never modified.
CREATE TABLE IF NOT EXISTS track_user (
    track_id    INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    bpm         REAL,
    camelot_key TEXT,
    sample_type TEXT,
    rating      INTEGER,          -- 1..5, NULL = unrated
    notes       TEXT,
    updated_at  TEXT NOT NULL
);

-- Every edit is logged so any batch can be undone exactly.
CREATE TABLE IF NOT EXISTS edit_batches (
    batch_id  TEXT PRIMARY KEY,
    label     TEXT,
    at        TEXT NOT NULL,
    n_tracks  INTEGER NOT NULL,
    undone    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS edit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES edit_batches(batch_id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL,
    field    TEXT NOT NULL,       -- 'tag+', 'tag-', or a track_user column
    old_json TEXT,
    new_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_edit_log_batch ON edit_log(batch_id);
CREATE INDEX IF NOT EXISTS idx_edit_log_track ON edit_log(track_id);

-- Sound fingerprint per track (see features.py). `vec` is NULL + `error` set when a file couldn't be analysed,
-- so it isn't retried forever. Re-running analysis only touches tracks without a current-version row.
CREATE TABLE IF NOT EXISTS track_features (
    track_id     INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    vec          BLOB,
    analysis_bpm REAL,
    analysis_key TEXT,            -- Camelot code
    key_conf     REAL,
    seconds      REAL,            -- how much audio was analysed
    error        TEXT,
    analyzed_at  TEXT NOT NULL
);

-- Version groups: files that are the same track (mp3/wav export, v2, FINAL...). Non-destructive: nothing is deleted or
-- renamed, groups only change how the library is *displayed* (stacked) and how "sent" is counted. User decisions are
-- `locked` so a re-scan never undoes them.
CREATE TABLE IF NOT EXISTS version_groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    primary_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,          -- 'duplicate' (same audio) | 'version' (same title, different file)
    confidence REAL,
    locked     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS version_members (
    track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES version_groups(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_version_members_group ON version_members(group_id);
CREATE TABLE IF NOT EXISTS version_exclusions (          -- "this is NOT a version of anything" (user said so)
    track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE
);

-- Tags the app added by itself (moods from audio, accepted genre suggestions). rejected=1 = user removed/declined it:
-- never added again. Source tells the UI to mark them (✦) so you can tell them from tags you typed.
CREATE TABLE IF NOT EXISTS auto_tags (
    track_id   INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    tag        TEXT NOT NULL,
    source     TEXT NOT NULL,         -- 'audio-mood' | 'genre-suggest'
    confidence REAL,
    rejected   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (track_id, tag)
);

CREATE TABLE IF NOT EXISTS saved_searches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    query_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Columns added after v1. Applied with ALTER TABLE because CREATE TABLE IF NOT EXISTS won't touch old DBs.
_ADDED_TRACK_COLUMNS = {
    "sample_type": "TEXT",  # 'loop' | 'one-shot' | 'track'  ('track' = a full song / beat)
    "bpm_source":  "TEXT",  # NULL/'analysis' = detected from audio, 'filename' = parsed from the file name
}


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)  # wait (not fail) if the import thread is mid-write
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Optional[Path] = None) -> Path:
    """Create the database file and any missing tables. Returns the DB path."""
    path = Path(db_path) if db_path else DB_PATH
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(tracks)")}
        for col, decl in _ADDED_TRACK_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {decl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_sample_type ON tracks(sample_type)")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    target = init_db(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"Database ready: {target}")
