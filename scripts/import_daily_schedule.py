#!/usr/bin/env python3
"""
Import a daily course schedule file into Postgres.

Run from the backend directory:
  python scripts/import_daily_schedule.py --course-slug mindful-foundations --file ../schedules/mindful-foundations.txt

Requires SUPABASE_DB_URL and an existing row in public.courses for the slug.
Replaces all existing schedule days for that course.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/import_daily_schedule.py` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.daily_schedule import parse_schedule_text, replace_schedule  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import daily course schedule into Postgres")
    parser.add_argument(
        "--course-slug",
        required=True,
        help="Course identifier (must exist in public.courses)",
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path to schedule text file (outside app code)",
    )
    args = parser.parse_args()

    path = args.file.resolve()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    days = parse_schedule_text(text)
    if not days:
        print("No schedule days parsed from file.", file=sys.stderr)
        sys.exit(1)

    count = replace_schedule(args.course_slug, days)
    day_range = f"{days[0].day_number}–{days[-1].day_number}"
    print(f"Imported {count} day(s) for {args.course_slug!r} (days {day_range}).")


if __name__ == "__main__":
    main()
