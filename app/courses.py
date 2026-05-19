"""
courses.py — Course catalog built from the rag/raw/courses/ directory structure.

Scans:
  rag/raw/base/                               always-on grounding (not a listed course)
  rag/raw/courses/<course_slug>/week-NN/      one course per slug directory

Returns plain dicts so callers can construct Pydantic models from them.
"""

import os
import re
from pathlib import Path
from typing import Optional


# Default: three levels up from backend/app/ project root rag/raw/courses
_DEFAULT_COURSES_DIR = Path(__file__).parent.parent.parent / "rag" / "raw" / "courses"


def _courses_dir() -> Path:
    env_val = os.getenv("COURSES_RAW_DIR", "")
    return Path(env_val) if env_val else _DEFAULT_COURSES_DIR


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _lesson_stem_to_title(stem: str) -> str:
    # Strip leading "lesson-NN-" prefix if present, e.g. "lesson-01-welcome" "Welcome"
    cleaned = re.sub(r"^lesson-\d+-", "", stem)
    return cleaned.replace("-", " ").replace("_", " ").title()


def _parse_week_number(dir_name: str) -> Optional[int]:
    m = re.match(r"week-(\d+)$", dir_name)
    if m:
        return int(m.group(1))
    return None


def list_courses() -> list[dict]:
    """Return a list of course summary dicts."""
    courses_dir = _courses_dir()
    if not courses_dir.exists():
        return []

    results = []
    for course_dir in sorted(courses_dir.iterdir()):
        if not course_dir.is_dir():
            continue
        course_slug = course_dir.name
        week_count = sum(
            1
            for d in course_dir.iterdir()
            if d.is_dir() and _parse_week_number(d.name) is not None
        )
        results.append(
            {
                "course_slug": course_slug,
                "title": _slug_to_title(course_slug),
                "description": (
                    f"{week_count} week{'s' if week_count != 1 else ''} of guided practice"
                ),
                "week_count": week_count,
            }
        )
    return results


def get_course_detail(course_slug: str) -> Optional[dict]:
    """Return full course detail dict with weeks and lessons, or None if not found."""
    courses_dir = _courses_dir()
    course_dir = courses_dir / course_slug
    if not course_dir.exists() or not course_dir.is_dir():
        return None

    weeks = []
    for week_dir in sorted(course_dir.iterdir()):
        if not week_dir.is_dir():
            continue
        week_number = _parse_week_number(week_dir.name)
        if week_number is None:
            continue

        lessons = []
        lesson_number = 1
        for lesson_file in sorted(week_dir.iterdir()):
            if lesson_file.suffix in {".txt", ".md"}:
                lessons.append(
                    {
                        "lesson_number": lesson_number,
                        "title": _lesson_stem_to_title(lesson_file.stem),
                        "filename": lesson_file.stem,
                    }
                )
                lesson_number += 1

        weeks.append(
            {
                "week_number": week_number,
                "title": f"Week {week_number}",
                "lessons": lessons,
            }
        )

    return {
        "course_slug": course_slug,
        "title": _slug_to_title(course_slug),
        "weeks": weeks,
    }
