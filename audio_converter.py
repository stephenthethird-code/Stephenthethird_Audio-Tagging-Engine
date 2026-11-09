# audio_converter.py

import argparse
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

    # Load the input file with its extension as format hint
    audio = AudioSegment.from_file(input_path, format=input_ext)

    export_params = {}

    # Optional: tweak default params per output format
    if output_ext == "mp3":
        export_params["bitrate"] = "192k"
    elif output_ext in {"m4a", "aac"}:
        # AAC in an m4a container usually
        export_params["codec"] = "aac"
        export_params["bitrate"] = "192k"

    audio.export(output_path, format=output_ext, **export_params)

    print(f"Converted '{input_path}' -> '{output_path}'")


def build_output_path(input_path: str, target_ext: str) -> str:
    """
    Build an output path next to the input file, with the target extension.
    Example: song.wav + 'm4a' -> song.m4a
    """
    base, _ = os.path.splitext(input_path)
    return f"{base}.{target_ext}"


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert audio files between formats (wav, mp3, m4a, flac, ogg, etc.). "
            "Requires ffmpeg to be installed."
        )
    )
    parser.add_argument(
        "input",
        help="Path to the input audio file (any ffmpeg-supported format).",
    )
    parser.add_argument(
        "-f",
        "--format",
        help=(
            "Target format/extension (e.g. mp3, wav, m4a, flac, ogg). "
            "If omitted, inferred from --output extension."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help=(
            "Path to the output file. If omitted, uses the same name as input "
            "with the new extension from --format."
        ),
    )

    args = parser.parse_args()

    input_path = args.input

    # Determine target extension and output path
    if args.output:
        output_path = args.output
        target_ext = get_extension(output_path)

        # If user gave an output path without extension, fall back to --format
        if not target_ext:
            if not args.format:
                raise SystemExit(
                    "When output file has no extension, you must specify --format."
                )
            target_ext = args.format.lower()
            output_path = f"{output_path}.{target_ext}"
    else:
        # No explicit output path: must infer from --format
        if not args.format:
            raise SystemExit(
                "You must specify either --output (with extension) or --format."
            )
        target_ext = args.format.lower()
        output_path = build_output_path(input_path, target_ext)

    convert_audio(input_path, output_path)


if __name__ == "__main__":
    main()
