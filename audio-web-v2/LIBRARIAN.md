# Librarian layer — design notes

## Evidence (what other tools do) → what v2 does
| Pattern seen in best-in-class tools | Source | v2 |
|---|---|---|
| Point the app at folders; everything on the drive becomes searchable "by feel, not filename"; 100% local processing | Tamber, public product description of its Librarian app | Import wizard, local-only, tags from names/folders |
| Mimic: make a sound to find a sound (query by sound) | Tamber (Librarian Pro); Sononym "record live audio as search input" | **Not built** (needs audio embeddings) |
| ML auto-categorisation + loop/one-shot flag | Sononym; Loopcloud AI tagging | Rule-based tags + type heuristic (`tagging.py`) |
| Similarity search | Sononym (spectrum/timbre/pitch/amplitude weights) | "More like this" by tags/tempo/key (metadata, not audio) |
| Exclude-tags, tag editor, collections, saved searches, tag counts | Loopcloud and other sample browsers | Exclude via shift-click, tag manager, collections, smart searches |
| Duplicate detection | Sononym | **Not built** |
| Users complain auto-tags can't be overridden and come back on rescan | KVR forum thread on Sononym | User layer is separate from detected values, so rescans never overwrite edits |

Not confirmed by the sources I could reach: how Librarian/Sononym/Atlas store edits, and whether they batch-edit.
Those parts of v2 (user layer, edit log, undo) are this project's own design.

## Data model (SQLite, schema v4)
- `tracks` — detected facts (path, duration, filename BPM, analysed BPM/key, sample_type). Re-scans may rewrite these.
- `track_tags(track_id, tag)` — flat tags; facet *kind* (type/instrument/genre/mood/year) is derived.
- `track_user(track_id, bpm, camelot_key, sample_type, rating, notes)` — **user layer**; wins over detected. NULL = "use detected".
- `edit_batches` + `edit_log(field: tag+ | tag- | coll+ | <column>, old_json, new_json)` — every edit, exactly reversible.
- `collections`/`collection_tracks`, `saved_searches(name, query_json)`.
Audio files are never modified, moved or renamed.

## UI flow
Import folder → (1) browse/choose → (2) preview: counts, formats, folders, new vs known → (3) progress (cancellable) →
summary with starter tag chips → Library. Library: search box (+chips of how it was understood) · sidebar facets
(type, BPM, key, rating, saved searches, collections, grouped tags) · rows (tick to select, shift-range) →
floating batch bar → Edit dialog · click a name → inspector (detected vs yours, reset, tags, rating, notes, history) ·
every edit shows an Undo toast.

## Where the code is
`audio_engine/librarian.py` (import, batch, undo, saved searches, inspector data) · `smart_search.py` (never-empty scoring) ·
`tagging.py` · `audio-web-v2/backend/library_api.py` (REST) · `audio-web-v2/frontend/library.js`.
Tests: `python -m unittest discover tests`.

## Sound analysis, "sounds like" and Mimic (v2.2)
- `features.py` — one 71-number fingerprint per track from the first 60 s: timbre (MFCC), brightness/spectrum, pitch (chroma), energy, rhythm
  (tempo on a circle, so 70 and 140 bpm are neighbours). Classic DSP via librosa + ffmpeg decode; no cloud, no model download. ~0.3 s/track.
- `analysis_job.py` — background, resumable: pending = tracks without a current fingerprint. Also fills detected key (and tempo when no file-name BPM).
  A BPM in the file name beats the audio estimate; your own edits beat both. Unreadable/silent files are recorded, not retried; unplugged drives stay pending.
- `similarity.py` — per-group z-score + cosine, weighted mean over the groups you leave on. "≈" on a track = sounds-like; **Mimic** = same, but the query is a
  clip you record or drop in. Tags/tempo/key matching remains as a fallback and as a toggle.
- Measured on 49 real tracks from the author's own library: same-audio re-exports (mp3 vs wav, v2 bounces) ranked each other #1 in every case;
  versions with a changed tempo ranked #5–#47. Small sample, so treat percentages as a ranking aid, not a probability.
- Mimic matches tone/energy/tempo/key-ish, **not melody**. Browser recording needs mic permission (works on localhost).

## Version stacks, moods, genres, send tracker, Discover (v2.3)
- **Version stacks** (`versions.py`): two signals. SAME AUDIO = fingerprint similarity >= 0.995 + same duration + >= 8 s (identical re-exports scored 1.000;
  the closest unrelated pair 0.718). SAME TITLE = file name minus tempo-like numbers, version words, brackets and author words (needs 6+ letters, full-length tracks only).
  Each stack shows once, best file on top (lossless > bitrate > "final/master" > newest). "Make main", "Not a version" and manual "Group" are locked and survive rescans. Nothing is deleted.
- **Moods** (`moods.py`): dark / bright / chill / hard / energetic / uplifting from audio features, relative to *your* library; heuristics, not a trained model.
  Added as tags marked ✦; remove one and it is never re-added.
- **Genres** (`genres.py`): genre tags come from names/folders/you. For the rest it *suggests* by kNN against tracks you labeled (needs 3+ per genre);
  nothing is applied until you accept. Declined suggestions never return.
- **Send tracker** (`sends.py`): uses your CRM `people` table. Version-aware ("heard v1" = heard the track). Recommends recipients by shared taste words + tier, and
  tracks for a contact (unheard, unpitched, rated). Weak until contacts carry genre/mood words in their tags.
- **Discover** (`shelves.py`): genre/mood tiles, never pitched, sounds like your favorites (4-5 star + hearted), remember these, recently added.

## Next steps (not built)
Audio-based similarity / Mimic (embeddings), duplicate finder, BPM/key analysis as a resumable background job,
watch folders for new files, write-back of tags to file metadata as an explicit opt-in export.
