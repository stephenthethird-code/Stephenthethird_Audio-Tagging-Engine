"""Library API for Audio Converter v2: search, suggestions, tags, collections, preview audio."""
import mimetypes
import sys
from pathlib import Path
from typing import List, Optional

from typing import Any, Dict

import os
import tempfile

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audio_engine import (analysis_job, features, genres, librarian, library, moods, search as faceted, sends, shelves,  # noqa: E402
                          smart_search, versions)
from audio_engine.db import connect, init_db  # noqa: E402

router = APIRouter(prefix="/api")
LIKED = "♥ Liked"
init_db()  # creates / upgrades the DB (adds collections + sample_type columns if missing)


@router.get("/search")
def api_search(q: str = "", tag: List[str] = Query(default=[]), exclude: List[str] = Query(default=[]),
               bpm_min: Optional[float] = None, bpm_max: Optional[float] = None, key: Optional[str] = None,
               type: Optional[str] = None, collection: Optional[str] = None, sort: str = "relevance",
               page: int = 1, per_page: int = 40, seed: Optional[int] = None, rating_min: Optional[int] = None,
               stack: bool = True, sent: Optional[str] = None):
    if sort not in {"relevance", "name", "bpm", "key", "duration", "newest", "random"}:
        raise HTTPException(400, "unknown sort")
    return smart_search.search(q, tag, exclude, bpm_min, bpm_max, key, type, collection, sort, page, per_page, seed, rating_min, stack, sent)


@router.get("/suggest")
def api_suggest(q: str = ""):
    return smart_search.autocomplete(q)


@router.get("/similar/{track_id}")
def api_similar(track_id: int, per_page: int = 40, page: int = 1, by: str = "auto", groups: Optional[str] = None):
    """More like this. by=auto|audio|tags; groups = comma list of timbre,spectrum,pitch,energy,rhythm."""
    return smart_search.similar_to(track_id, per_page, by=by, groups=groups.split(",") if groups else None, page=page)


@router.post("/mimic")
async def api_mimic(file: UploadFile = File(...), groups: Optional[str] = Form(None)):
    """Search by sound: upload/record a short clip (hum, beatbox, a sample from elsewhere) -> closest tracks in the library."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "That clip is empty.")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "Clip is too large (max 25 MB).")
    fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(file.filename or "")[1] or ".webm")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            info = features.extract(features.decode(tmp, seconds=30))
        except Exception as exc:
            raise HTTPException(400, "Couldn't read that clip as audio. Try a WAV, MP3, M4A or a browser recording."
                                     + (" (it looks silent)" if "silent" in str(exc) else ""))
    finally:
        os.unlink(tmp)
    return smart_search.sound_search(info["vec"], info, groups.split(",") if groups else None)


@router.get("/analysis")
def api_analysis_status():
    return analysis_job.status()


@router.post("/analysis/start")
def api_analysis_start(ids: Optional[List[int]] = Body(None, embed=True), limit: Optional[int] = Body(None, embed=True)):
    analysis_job.start(ids, limit)
    return analysis_job.status()


@router.post("/analysis/cancel")
def api_analysis_cancel():
    return {"cancelled": analysis_job.cancel()}


@router.get("/stats")
def api_stats():
    conn = connect()
    try:
        one = lambda sql: conn.execute(sql).fetchone()[0]
        return {"tracks": one("SELECT COUNT(*) FROM tracks"),
                "tagged": one("SELECT COUNT(DISTINCT track_id) FROM track_tags"),
                "with_bpm": one("SELECT COUNT(*) FROM tracks WHERE bpm IS NOT NULL"),
                "with_key": one("SELECT COUNT(*) FROM tracks WHERE camelot_key IS NOT NULL OR key IS NOT NULL")}
    finally:
        conn.close()


@router.get("/tags")
def api_tags():
    return faceted.list_tags()


@router.post("/autotag")
def api_autotag():
    out = faceted.autotag_library()
    smart_search.invalidate()
    return out


@router.get("/collections")
def api_collections():
    return faceted.list_collections()


@router.post("/collections")
def api_create_collection(name: str = Body(..., embed=True)):
    try:
        return {"id": faceted.create_collection(name), "name": name.strip()}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/collections/{name}/tracks")
def api_collect(name: str, ids: List[int] = Body(..., embed=True)):
    return {"added": faceted.add_to_collection(name, ids)}


@router.delete("/collections/{name}/tracks/{track_id}")
def api_uncollect(name: str, track_id: int):
    faceted.remove_from_collection(name, [track_id])
    return {"ok": True}


@router.get("/audio/{track_id}")
def api_audio(track_id: int):
    """Stream a library file for preview (by id only, so arbitrary paths can't be requested)."""
    track = library.get_track(track_id)
    if track is None:
        raise HTTPException(404, "No such track")
    path = Path(track["path"])
    if not path.is_file():
        raise HTTPException(404, "File is missing — is the drive it lives on connected?")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=mime)


# ---------------------------------------------------------------- librarian layer (v2.1)
def _bad(fn, *a, **k):
    """Turn validation errors into clean 400s the UI can show."""
    try:
        return fn(*a, **k)
    except (ValueError, NotADirectoryError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc) if not isinstance(exc, NotADirectoryError) else f"Not a folder: {exc}")


@router.get("/fs")
def api_fs(path: Optional[str] = None):
    """Folder picker for the import wizard (local single-user app: the server only lists folder names)."""
    return _bad(librarian.list_dirs, path)


@router.post("/import/scan")
def api_import_scan(path: str = Body(..., embed=True)):
    return _bad(librarian.scan_folder, path)


@router.post("/import/start")
def api_import_start(path: str = Body(..., embed=True), deep: bool = Body(False, embed=True)):
    return _bad(librarian.start_import, path, deep).public()


@router.get("/import/{job_id}")
def api_import_status(job_id: str):
    job = librarian.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown import")
    return job.public()


@router.post("/import/{job_id}/cancel")
def api_import_cancel(job_id: str):
    return {"cancelled": librarian.cancel_job(job_id)}


@router.post("/batch")
def api_batch(ids: List[int] = Body(...), add_tags: List[str] = Body([]), remove_tags: List[str] = Body([]),
              set: Dict[str, Any] = Body({}), clear: List[str] = Body([]), collection: Optional[str] = Body(None)):
    """Edit many tracks at once (non-destructive, undoable). One endpoint serves the inspector (1 id) and batch bar."""
    return _bad(librarian.apply_batch, ids, add_tags, remove_tags, set, clear, collection)


@router.post("/undo/{batch_id}")
def api_undo(batch_id: str):
    return _bad(librarian.undo_batch, batch_id)


@router.get("/history")
def api_history():
    return librarian.recent_edits()


@router.get("/tracks/{track_id}")
def api_track(track_id: int):
    detail = librarian.track_detail(track_id)
    if detail is None:
        raise HTTPException(404, "No such track")
    return detail


@router.post("/tags/rename")
def api_rename_tag(old: str = Body(...), new: str = Body(...)):
    return _bad(librarian.rename_tag_logged, old, new)


@router.post("/tags/delete")
def api_delete_tag(tag: str = Body(..., embed=True)):
    return _bad(librarian.delete_tag, tag)


@router.get("/saved-searches")
def api_saved_searches():
    return librarian.list_saved_searches()


@router.post("/saved-searches")
def api_save_search(name: str = Body(...), query: Dict[str, Any] = Body(...)):
    return {"id": _bad(librarian.save_search, name, query)}


@router.delete("/saved-searches/{search_id}")
def api_delete_saved_search(search_id: int):
    librarian.delete_saved_search(search_id)
    return {"ok": True}


# ---------------------------------------------------------------- v2.3: stacks, moods, genres, send tracker, discover
@router.get("/shelves")
def api_shelves(seed: Optional[int] = None):
    return shelves.build(seed)


@router.get("/similar-set")
def api_similar_set(ids: str, per_page: int = 40):
    return shelves.similar_to_set([int(i) for i in ids.split(",") if i.strip().isdigit()], per_page)


@router.post("/versions/rebuild")
def api_versions_rebuild():
    return versions.rebuild()


@router.get("/versions/groups")
def api_versions_groups(offset: int = 0, limit: int = 20):
    return versions.list_groups(offset, limit)


@router.get("/versions/group/{group_id}")
def api_versions_group(group_id: int):
    g = versions.group_detail(group_id)
    if g is None:
        raise HTTPException(404, "No such group")
    return g


@router.post("/versions/{group_id}/primary")
def api_versions_primary(group_id: int, track_id: int = Body(..., embed=True)):
    _bad(versions.set_primary, group_id, track_id)
    return versions.group_detail(group_id)


@router.post("/versions/ungroup")
def api_versions_ungroup(track_id: int = Body(..., embed=True)):
    versions.ungroup(track_id)
    return {"ok": True}


@router.post("/versions/merge")
def api_versions_merge(ids: List[int] = Body(..., embed=True)):
    return versions.group_detail(_bad(versions.merge, ids))


@router.post("/moods/apply")
def api_moods_apply():
    return moods.apply()


@router.get("/genres")
def api_genres():
    return genres.overview()


@router.get("/genres/suggestions")
def api_genre_suggestions(limit: int = 60, min_conf: float = 0.5):
    return genres.suggestions(limit, min_conf)


@router.post("/genres/accept")
def api_genres_accept(items: List[Dict[str, Any]] = Body(..., embed=True)):
    return genres.accept(items)


@router.post("/genres/reject")
def api_genres_reject(items: List[Dict[str, Any]] = Body(..., embed=True)):
    return {"rejected": genres.reject(items)}


@router.get("/people")
def api_people(q: Optional[str] = None, limit: int = 40, offset: int = 0):
    return sends.list_people(q, limit, offset)


@router.post("/people")
def api_save_person(person: Dict[str, Any] = Body(...)):
    return {"id": _bad(sends.save_person, person)}


@router.get("/people/{person_id}/suggestions")
def api_person_suggestions(person_id: int, top_n: int = 12):
    return _bad(sends.suggest_tracks_for_person, person_id, top_n)


@router.get("/tracks/{track_id}/send-suggestions")
def api_send_suggestions(track_id: int, top_n: int = 8, include_sent: bool = False):
    return _bad(sends.suggest_recipients, track_id, top_n, include_sent)


@router.get("/tracks/{track_id}/sends")
def api_track_sends(track_id: int):
    return sends.sends_for_track(track_id)


@router.post("/sends")
def api_record_sends(track_ids: List[int] = Body(...), person_id: int = Body(...), note: Optional[str] = Body(None)):
    return _bad(sends.record_sends, track_ids, person_id, note)


@router.post("/sends/delete")
def api_delete_sends(ids: List[int] = Body(..., embed=True)):
    return {"deleted": sends.delete_sends(ids)}
