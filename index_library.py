# index_library.py
"""
Scan folder(s) for audio files and store them in the library database.

  python3 index_library.py /path/to/audio            # full pass: metadata + BPM + key
  python3 index_library.py /path/to/audio --quick    # fast metadata-only pass (no BPM/key)
  python3 index_library.py /path/to/audio            # re-run later: unchanged files are skipped
"""
import argparse
import logging
import sys
import time
from pathlib import Path

from audio_engine.config import DB_PATH
from audio_engine.db import connect, init_db
from audio_engine.library import index_file, index_folder


def main() -> int:
    parser = argparse.ArgumentParser(description="Index audio files into the library database.")
    parser.add_argument("folders", nargs="+", help="folder(s) to scan")
    parser.add_argument("--quick", action="store_true",
                        help="metadata only (skips slow BPM/key analysis; a later full run fills them in)")
    parser.add_argument("--force", action="store_true", help="re-analyze even unchanged files")
    parser.add_argument("--db", default=None, help=f"database path (default: {DB_PATH})")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("index_library")

    db_path = init_db(args.db)
    log.info("Database: %s", db_path)

    totals = {"added": 0, "updated": 0, "skipped": 0, "failed": 0}
    started = time.time()
    for folder in args.folders:
        try:
            if Path(folder).expanduser().is_file():  # a single audio file works too
                conn = connect(db_path)
                try:
                    _, status = index_file(folder, deep=not args.quick, force=args.force, conn=conn)
                    conn.commit()
                finally:
                    conn.close()
                log.info("%-7s %s", status, folder)
                totals[status] += 1
                continue
            counts = index_folder(folder, deep=not args.quick, force=args.force, db_path=db_path)
        except (NotADirectoryError, FileNotFoundError):
            log.error("Not a folder: %s", folder)
            totals["failed"] += 1
            continue
        for name, n in counts.items():
            totals[name] += n

    log.info("Done in %.0fs: %d added, %d updated, %d unchanged, %d failed",
             time.time() - started, totals["added"], totals["updated"], totals["skipped"], totals["failed"])
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
