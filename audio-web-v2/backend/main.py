import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

import librosa
import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mutagen import File as MutagenFile
from starlette.background import BackgroundTask

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ANALYSIS_SECONDS = 120  # only analyze the first 2 minutes so big files stay fast

# extension -> (ffmpeg codec args, mime type)
FORMATS = {
    "wav": (["-c:a", "pcm_s16le"], "audio/wav"),
    "mp3": (["-c:a", "libmp3lame", "-b:a", "320k"], "audio/mpeg"),
    "m4a": (["-c:a", "aac", "-b:a", "256k"], "audio/mp4"),
    "flac": (["-c:a", "flac"], "audio/flac"),
    "ogg": (["-c:a", "libopus", "-b:a", "192k"], "audio/ogg"),  # Ogg Opus (this ffmpeg has no Vorbis encoder)
    "aac": (["-c:a", "aac", "-b:a", "256k", "-f", "adts"], "audio/aac"),
}

PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler key profiles
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Camelot wheel: B = major, A = minor (pitch classes use sharps, matching PITCH_CLASSES)
CAMELOT_MAJOR = {
    "B": "1B", "F#": "2B", "C#": "3B", "G#": "4B", "D#": "5B", "A#": "6B",
    "F": "7B", "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B",
}
CAMELOT_MINOR = {
    "G#": "1A", "D#": "2A", "A#": "3A", "F": "4A", "C": "5A", "G": "6A",
    "D": "7A", "A": "8A", "E": "9A", "B": "10A", "F#": "11A", "C#": "12A",
}

MAX_FILENAME_LEN = 200
BITRATE_PATTERN = re.compile(r"^\d{2,3}k$")  # e.g. 192k, 320k

app = FastAPI(title="Audio Converter v2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local use only; lets you open index.html straight from disk
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Output-Filename"],  # lets the page read the final name when opened from disk
)


# ---------- helpers ----------

def save_upload(upload: UploadFile, folder: str) -> str:
    """Write the uploaded file into `folder` and return its path."""
    suffix = Path(upload.filename or "").suffix or ".bin"
    path = os.path.join(folder, "input" + suffix)
    with open(path, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    return path


def run_ffmpeg(args: list) -> None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise HTTPException(500, "ffmpeg is not installed. Run: brew install ffmpeg")
    if result.returncode != 0:
        raise HTTPException(422, "Could not process this file: " + result.stderr.strip()[-300:])


def parse_key(key):
    """'C# minor' -> ('C#', 'minor'). Returns None if the key can't be understood."""
    match = re.match(r"^\s*([A-G]#?)\s+(major|minor)\s*$", key or "", re.IGNORECASE)
    if not match:
        return None
    return match.group(1).upper(), match.group(2).lower()


def camelot_key(key):
    """'C# minor' -> '12A', 'C major' -> '8B'. Returns None if unknown."""
    parsed = parse_key(key)
    if parsed is None:
        return None
    pitch, mode = parsed
    return (CAMELOT_MAJOR if mode == "major" else CAMELOT_MINOR).get(pitch)


def short_key(key):
    """'C# minor' -> 'C#m', 'C major' -> 'C'. Returns None if unknown."""
    parsed = parse_key(key)
    if parsed is None:
        return None
    pitch, mode = parsed
    return pitch + ("m" if mode == "minor" else "")


def sanitize_filename(name: str) -> str:
    """Remove characters that are unsafe in filenames (path separators, control chars, etc.)."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    return name.strip().strip(".")


def build_codec_args(fmt: str, bitrate=None) -> list:
    """Codec args for `fmt`; swaps in `bitrate` (e.g. '192k') for lossy formats that have one."""
    args = list(FORMATS[fmt][0])
    if bitrate and BITRATE_PATTERN.match(bitrate) and "-b:a" in args:
        args[args.index("-b:a") + 1] = bitrate
    return args


def build_output_name(template, original_stem: str, bpm, key, ext: str) -> str:
    """
    Fill in a naming template. Supports {name}, {bpm}, {key}, {ext}.
    template=None keeps the old behaviour ("<name>.<ext>"); a blank or broken template
    falls back to "<name>_converted.<ext>".
    """
    stem = sanitize_filename(original_stem or "") or "audio"
    if template is None:
        return f"{stem}.{ext}"
    fallback = f"{stem}_converted.{ext}"

    try:
        try:
            bpm_text = f"{float(bpm):g}"
        except (TypeError, ValueError):
            bpm_text = "unknown"
        key_text = short_key(key) or "unknown"

        result = (
            template.strip()
            .replace("{name}", stem)
            .replace("{bpm}", bpm_text)
            .replace("{key}", key_text)
            .replace("{ext}", ext)
        )
        result = sanitize_filename(result)
        if not result or result.lower() == ext:
            return fallback
        if not result.lower().endswith("." + ext):
            result += "." + ext
        if len(result) > MAX_FILENAME_LEN:
            return fallback
        return result
    except Exception:
        return fallback


def format_duration(seconds):
    """83.4 -> '1:23'. Returns None if unknown."""
    if seconds is None:
        return None
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def read_metadata(path: str, original_name: str = None) -> dict:
    """Filename, duration, sample rate, channels (soundfile, then mutagen) and tags (mutagen)."""
    info = {
        "filename": original_name or os.path.basename(path),
        "duration_sec": None,
        "samplerate": None,
        "channels": None,
        "tags": {},
    }

    # soundfile reads exact properties for wav / flac / ogg / mp3 (not m4a / aac)
    try:
        with sf.SoundFile(path) as f:
            info["samplerate"] = f.samplerate
            info["channels"] = f.channels
            info["duration_sec"] = len(f) / float(f.samplerate)
    except Exception:
        pass

    # mutagen: tags, plus fallback audio properties for formats soundfile can't open
    try:
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            info["duration_sec"] = info["duration_sec"] or getattr(audio.info, "length", None)
            info["samplerate"] = info["samplerate"] or getattr(audio.info, "sample_rate", None)
            info["channels"] = info["channels"] or getattr(audio.info, "channels", None)
    except Exception:
        pass

    try:
        easy = MutagenFile(path, easy=True)  # easy=True gives friendly names: artist, title, album...
        if easy is not None and easy.tags:
            for key, value in easy.tags.items():
                if isinstance(value, (list, tuple)):
                    value = ", ".join(str(v) for v in value)
                info["tags"][str(key).lower()] = str(value)
    except Exception:
        pass

    if info["duration_sec"] is not None:
        info["duration_sec"] = round(float(info["duration_sec"]), 3)
    return info


def detect_bpm_and_key(path: str, workdir: str) -> dict:
    """Estimate tempo (BPM) and musical key. Returns {"bpm": float|None, "key": str|None}."""
    result = {"bpm": None, "key": None}

    # Decode a mono 22.05 kHz excerpt with ffmpeg so every format works the same way.
    wav = os.path.join(workdir, "analysis.wav")
    run_ffmpeg(["-i", path, "-vn", "-t", str(ANALYSIS_SECONDS), "-ac", "1", "-ar", "22050", wav])

    try:
        y, sr = sf.read(wav, dtype="float32")
        if y.size == 0 or not np.any(y):
            return result  # silence: nothing to detect

        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(np.atleast_1d(tempo)[0])
        if tempo > 0:
            result["bpm"] = round(tempo, 1)

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
        best_score = -2.0
        for i in range(12):
            for profile, mode in ((MAJOR_PROFILE, "major"), (MINOR_PROFILE, "minor")):
                score = np.corrcoef(chroma, np.roll(profile, i))[0, 1]
                if score > best_score:
                    best_score = score
                    result["key"] = f"{PITCH_CLASSES[i]} {mode}"
    except Exception:
        pass  # analysis problems shouldn't break the whole request

    return result


# ---------- endpoints ----------

@app.post("/analyze")
def analyze(file: UploadFile = File(...)):
    with tempfile.TemporaryDirectory() as folder:
        path = save_upload(file, folder)
        meta = read_metadata(path, original_name=file.filename)
        analysis = detect_bpm_and_key(path, folder)
        return {
            "filename": meta["filename"],
            "duration": meta["duration_sec"],  # seconds
            "duration_formatted": format_duration(meta["duration_sec"]),  # "m:ss"
            "samplerate": meta["samplerate"],
            "channels": meta["channels"],
            "bpm": analysis["bpm"],
            "key": analysis["key"],
            "camelot_key": camelot_key(analysis["key"]),  # e.g. "12A"
            "tags": meta["tags"],  # e.g. {"artist": "...", "title": "...", "album": "..."}
        }


@app.post("/convert")
def convert(
    file: UploadFile = File(...),
    format: str = Form(...),
    naming_template: str = Form(None),  # e.g. "{name}_{bpm}bpm_{key}.{ext}"
    bpm: str = Form(None),  # from /analyze, used only to fill the template
    key: str = Form(None),  # from /analyze, e.g. "C# minor"
    bitrate: str = Form(None),  # e.g. "320k"; applies to mp3 / m4a / aac / ogg, ignored otherwise
):
    fmt = format.lower().lstrip(".")
    if fmt not in FORMATS:
        raise HTTPException(400, f"Unsupported format '{format}'. Choose from: {', '.join(FORMATS)}")
    mime = FORMATS[fmt][1]
    codec_args = build_codec_args(fmt, bitrate)

    folder = tempfile.mkdtemp()
    try:
        path = save_upload(file, folder)
        out_path = os.path.join(folder, "output." + fmt)
        run_ffmpeg(["-i", path, "-vn", "-map_metadata", "0"] + codec_args + [out_path])
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    download_name = build_output_name(
        naming_template, Path(file.filename or "audio").stem, bpm, key, fmt
    )
    return FileResponse(
        out_path,
        media_type=mime,
        filename=download_name,
        headers={"X-Output-Filename": quote(download_name)},
        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
    )


# v2: library search / tags / collections (routes under /api). Must be registered before the static mount.
from library_api import router as library_router  # noqa: E402
app.include_router(library_router)

# Serves the frontend at http://127.0.0.1:8766/ (must come after the API routes)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
