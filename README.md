# Audio Converter v2 — a local library for your audio

A local web app that turns a folder of audio (beats, samples, stems, demos) into a searchable, taggable library, and still
converts between formats. Everything runs on your machine: your files are never uploaded, moved, renamed or modified.

**The idea:** a search box that always answers. Instead of filtering to "(no results)", every track is *scored* against what you typed, so
you get the closest matches, an explanation of what was relaxed, and "try instead" suggestions drawn from your own library.

## Features

- **Import wizard** — pick a folder, preview what's new (counts, formats, biggest folders), import in the background with progress and cancel.
- **Search that never dead-ends** — free text ("dark piano 140", "trap 2024", "130-150", "8A", a track name), typo correction, removable chips showing how the query
  was understood, closest-match ranking, half/double-time tempo matching, Camelot key compatibility.
- **Sound analysis (local, no cloud, no model download)** — a 71-number fingerprint per track (timbre, brightness, pitch, energy, rhythm) plus tempo and key.
  Resumable background job (~0.2 s/track on Apple silicon).
- **"Sounds like" and search by sound** — find tracks that sound like a track, or like a clip you record, drop in, or hum. Toggle which aspects count.
- **Non-destructive editing** — tags, type, BPM, key, rating, notes: single-track inspector and batch edit. Your edits sit *on top of* detected values
  (reset any time) and every edit is undoable.
- **Version stacks** — an mp3 and a wav of the same export, "v2", "FINAL" collapse into one row (best file on top). Your decisions are locked and survive rescans.
- **Moods** — dark / bright / chill / hard / energetic / uplifting from the audio, relative to your own library; marked ✦, and removing one is permanent.
- **Genres** — genre tags from names/folders/you, plus *suggestions* learned from the tracks you've labeled. Nothing is applied until you accept.
- **Send tracker** — log who has heard what; recommends who to send a track to (shared taste + tier) and what to send a contact next. Version-aware.
- **Discover home** — shelves built from your library: genre/mood tiles, never pitched, sounds like your favorites, remember these, recently added.
- **Collections, saved searches, likes, preview player** (with seeking).
- **Converter** — drag-and-drop format conversion (WAV, MP3, M4A, FLAC, OGG, AAC) with naming templates.

## Quick start

Requires Python 3.9+ and [ffmpeg](https://ffmpeg.org/) on your PATH.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./audio-web-v2/run.sh            # open http://127.0.0.1:8766
```

No music handy? Generate 50 synthetic demo tracks (no copyrighted audio) and import that folder:

```bash
python3 scripts/make_demo_library.py demo_audio 50
```

Then **Import folder** → `demo_audio` → tick *analyze the sound* → **Start exploring**.

## Verify it works

```bash
python3 -m unittest discover tests                      # 34 unit tests (search, edits/undo, analysis, stacks, moods, genres, sends)

# end-to-end, through the real HTTP API, on a THROWAWAY database:
AUDIO_LIBRARY_DB=/tmp/smoke.sqlite ./audio-web-v2/run.sh &
python3 scripts/smoke_test.py --folder demo_audio       # ~65 checks: import → analysis → search → similar → Mimic → edits → stacks → moods → genres → sends → shelves
```

Verification so far (author's machine: macOS, Apple silicon, Python 3.9, ffmpeg 9): the unit tests and the smoke test pass; the smoke test also passes
(67/67 checks) on 50 real, mixed mp3/wav tracks imported into an empty library — import 0.5 s, analysis 10 s, every search returning results in under 10 ms.
Not yet tested on Windows or Linux, or on libraries beyond a couple of thousand tracks.

## How it works

```
frontend (HTML/CSS/JS) ──HTTP──▶ FastAPI (audio-web-v2/backend) ──▶ audio_engine/ (pure Python)
                                                                       ├─ library.py / librarian.py   import, tags, batch edit + undo, saved searches
                                                                       ├─ smart_search.py             parse → score → rank (never empty), autocomplete, "more like this"
                                                                       ├─ features.py + analysis_job.py   fingerprint, tempo, key; resumable background job
                                                                       ├─ similarity.py               per-group cosine over fingerprints
                                                                       ├─ versions.py / moods.py / genres.py / sends.py / shelves.py
                                                                       └─ db.py                       SQLite schema + in-place migrations
```

- **Two layers, not one:** `tracks` holds what was *detected*; `track_user` holds what *you decided*. Effective value = yours if set, else detected. Re-scans never overwrite your edits.
- **Every edit is logged** (`edit_log`) with old and new values, so any batch is exactly reversible — tags, fields, collection adds, genre acceptance.
- **Version stacks** use two conservative signals: identical audio (similarity ≥ 0.995, same duration, ≥ 8 s) or an identical *title* once tempo, version words, brackets and your own name are stripped.
- **Search** scores each track per constraint (tag, word, tempo, key, type, rating, sent) and ranks by weighted satisfaction; "exact" matches first, then the closest.

## Honest limits

- Sound similarity is classic signal processing (librosa), not a learned model: good at tone/energy/tempo/key, not at musical meaning. **Mimic does not match melodies.**
- Mood tags are rule-based heuristics judged against your own library. Genre suggestions need ≥ 3 labeled tracks per genre and only ever *suggest*.
- Version stacking by title can be wrong (e.g. two different songs with near-identical names). Uncertain stacks are listed first in *Review stacks*; "Not a version" fixes one permanently.
- Recipient suggestions are only as good as the taste words on your contacts (e.g. "trap", "rnb"); without them they fall back to tier.
- Import and analysis jobs live in server memory; restarting mid-run is safe (re-run resumes), but progress isn't persisted.
- The tempo in a file name wins over the audio estimate (beat trackers confuse half/double time).

## Privacy & security

Local-only by design: the server binds to `127.0.0.1`, there is no authentication, and the folder picker lists folder names on the machine running it. **Do not expose it to a network.**
Your library database, contacts and settings live in `db/` and are git-ignored. Contacts can be imported from a CSV with `import_people.py`.

## Configuration

| Setting | How |
|---|---|
| Database location | `AUDIO_LIBRARY_DB=/path/to/library.sqlite` (default `db/library.sqlite`) |
| Your own name/handle, stripped when comparing titles (e.g. "prod jane") | `db/settings.json` → `{"author_words": ["jane"]}` or `AUDIO_LIBRARY_AUTHOR_WORDS=jane` |

## Layout

`audio_engine/` engine · `audio-web-v2/` web app + design notes ([LIBRARIAN.md](audio-web-v2/LIBRARIAN.md)) · `tests/` unit tests · `scripts/` smoke test + demo audio ·
`library_cli.py`, `index_library.py`, `import_people.py` command-line tools · `audio_gui.py`, `pretty_gui.py`, `audio_converter.py` earlier desktop prototypes.

## Roadmap ideas

Waveform + audition at project tempo · reveal in Finder / drag to DAW · batch convert from the library · "what fits this beat" (key/tempo-compatible tracks) ·
watch folders · a learned audio embedding for better similarity.

## Credits

Inspired by the organization and search ideas of sample managers such as Sononym and Loopcloud; this is an independent implementation.
License: [MIT](LICENSE).
