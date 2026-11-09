# import_people.py
"""
Load contacts from your CRM spreadsheet (CSV export) into the `people` table. Safe to re-run:
people are matched on email (else name), and tags are merged.

  python3 import_people.py "/path/to/your-contacts.csv"

Expected columns: Name, Role, Tier, Location, Company, Email/Contact, Notes / Intel, Status
Tags come from Role ("role:producer"), Location ("loc:nyc") and genre words in the Notes
("rnb", "trap", "hip hop", ...). Edit GENRE_WORDS below to teach it more.
"""
import argparse
import csv
import logging
import re
import sys

from audio_engine.config import DB_PATH
from audio_engine.db import connect, init_db
from audio_engine.library import add_or_update_person

GENRE_WORDS = {
    "rnb": r"\brnb\b|\br&b\b",
    "hip hop": r"\bhip[ -]?hop\b",
    "trap": r"\btrap\b",
    "pop": r"\bpop\b",
    "rock": r"\brock\b",
    "emo": r"\bemo\b",
    "indie": r"\bindie\b",
    "drill": r"\bdrill\b",
    "country": r"\bcountry\b",
    "folk": r"\bfolk\b",
    "dance": r"\bdance\b",
    "christian": r"\bchristian\b",
    "loops": r"\bloops?\b",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLACEHOLDERS = {"", "-", "#", "ig", "?", "????"}


def clean(value) -> str:
    value = (value or "").strip()
    return "" if value in PLACEHOLDERS else value


def parse_tier(value: str):
    match = re.match(r"^([1-5])\b", value.strip())
    return int(match.group(1)) if match else None  # "Producer", "8 - Network" etc. -> unknown


def build_tags(role: str, location: str, notes: str):
    tags = []
    for part in re.split(r"[+/,]", role.lower()):
        part = part.strip()
        if part:
            tags.append("role:" + ("producer" if part == "prod" else part))
    for part in re.split(r"[/,]", location.lower()):
        part = part.strip()
        if part:
            tags.append("loc:" + part)
    text = notes.lower()
    tags += [genre for genre, pattern in GENRE_WORDS.items() if re.search(pattern, text)]
    return tags


def main() -> int:
    parser = argparse.ArgumentParser(description="Import contacts from a CSV into the people table.")
    parser.add_argument("csv_path")
    parser.add_argument("--db", default=None, help=f"database path (default: {DB_PATH})")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db_path = init_db(args.db)
    conn = connect(db_path)
    imported = skipped = 0
    try:
        with open(args.csv_path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                name = clean(row.get("Name"))
                if not name:
                    skipped += 1
                    continue
                email = clean(row.get("Email/Contact"))
                if not EMAIL_RE.match(email):
                    email = ""  # "-", "IG", phone numbers, "See Old Rolo" ... aren't emails
                role, location = clean(row.get("Role")), clean(row.get("Location"))
                notes = clean(row.get("Notes / Intel"))
                add_or_update_person(
                    name, email, build_tags(role, location, notes),
                    role=role or None, tier=parse_tier(clean(row.get("Tier"))),
                    location=location or None, company=clean(row.get("Company")) or None,
                    notes=notes or None, status=clean(row.get("Status")) or None, conn=conn,
                )
                imported += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    finally:
        conn.close()

    logging.info("Read %d rows (%d without a name skipped). people table now has %d unique people.",
                 imported, skipped, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
