# converter_core.py

import os
from pydub import AudioSegment


def get_extension(path: str) -> str:
    """
    Return the lowercase file extension without the dot, or empty string if none.
    """
    return os.path.splitext(path)[1].lower().lstrip(".")


def convert_audio(input_path: str, output_path: str) -> None:
    """
    Convert audio file from one format to another using ffmpeg via pydub.

    This supports many formats (wav, mp3, m4a, flac, ogg, etc.) as long as
    ffmpeg on your system can read/write them.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    input_ext = get_extension(input_path)
    output_ext = get_extension(output_path)

    if not input_ext:
        raise ValueError(f"Cannot infer input format (missing extension): {input_path}")
    if not output_ext:
        raise ValueError(f"Cannot infer output format (missing extension): {output_path}")

    audio = AudioSegment.from_file(input_path, format=input_ext)

    export_params = {}

    if output_ext == "mp3":
        export_params["bitrate"] = "192k"
    elif output_ext in {"m4a", "aac"}:
        export_params["codec"] = "aac"
        export_params["bitrate"] = "192k"

    audio.export(output_path, format=output_ext, **export_params)
