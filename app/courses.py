"""
courses.py — Course catalog service.

Primary source: Supabase course tables when SUPABASE_DB_URL is configured.
Fallback: rag/raw/courses/ directory structure for local development.

Returns plain dicts so callers can construct Pydantic models from them.
"""

import os
import re
from pathlib import Path
from typing import Optional

from app.config import SUPABASE_DB_URL, logger

try:
    from psycopg.errors import UndefinedTable
except ImportError:  # pragma: no cover
    UndefinedTable = ()  # type: ignore[misc, assignment]


def _log_db_fallback(context: str, exc: Exception) -> None:
    """Filesystem fallback is expected until course catalog SQL is applied."""
    if isinstance(exc, UndefinedTable) or "does not exist" in str(exc).lower():
        logger.warning("%s: using filesystem course catalog (%s)", context, exc)
        return
    logger.exception("%s: falling back to filesystem course catalog", context)


# Default: three levels up from backend/app/ project root rag/raw/courses
_DEFAULT_COURSES_DIR = Path(__file__).parent.parent.parent / "rag" / "raw" / "courses"


def _get_db_connection():
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")

    import psycopg

    return psycopg.connect(
        SUPABASE_DB_URL,
        autocommit=True,
        connect_timeout=5,
        prepare_threshold=None,
    )


def _default_description(week_count: int) -> str:
    return f"{week_count} week{'s' if week_count != 1 else ''} of guided practice"


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


def _list_courses_from_db() -> list[dict]:
    with _get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.course_slug,
                c.title,
                c.description,
                COUNT(DISTINCT w.id) AS week_count,
                p.provider_price_id,
                p.unit_amount_cents,
                p.currency
            FROM public.courses c
            LEFT JOIN public.course_weeks w ON w.course_slug = c.course_slug
            LEFT JOIN public.course_products p
                ON p.course_slug = c.course_slug
               AND p.is_active = true
            WHERE c.is_published = true
            GROUP BY
                c.course_slug,
                c.title,
                c.description,
                p.provider_price_id,
                p.unit_amount_cents,
                p.currency
            ORDER BY c.title, c.course_slug
            """
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        week_count = int(row[3] or 0)
        description = str(row[2] or "").strip() or _default_description(week_count)
        results.append(
            {
                "course_slug": row[0],
                "title": row[1],
                "description": description,
                "week_count": week_count,
                "price_id": row[4],
                "unit_amount_cents": row[5],
                "currency": row[6],
            }
        )

    return results


def _course_detail_from_db(course_slug: str) -> Optional[dict]:
    with _get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT title
            FROM public.courses
            WHERE course_slug = %s
              AND is_published = true
            """,
            (course_slug,),
        )
        course_row = cur.fetchone()
        if course_row is None:
            return None

        cur.execute(
            """
            SELECT
                w.week_number,
                w.title AS week_title,
                l.lesson_number,
                l.title AS lesson_title,
                l.content_ref
            FROM public.course_weeks w
            LEFT JOIN public.course_lessons l ON l.week_id = w.id
            WHERE w.course_slug = %s
            ORDER BY w.week_number, l.lesson_number
            """,
            (course_slug,),
        )
        rows = cur.fetchall()

    weeks_by_number: dict[int, dict] = {}
    for week_number, week_title, lesson_number, lesson_title, content_ref in rows:
        week = weeks_by_number.setdefault(
            int(week_number),
            {
                "week_number": int(week_number),
                "title": str(week_title or f"Week {week_number}"),
                "lessons": [],
            },
        )

        if lesson_number is None:
            continue

        filename = str(content_ref).strip() if content_ref else f"lesson-{int(lesson_number):02d}"
        lesson_title_value = str(lesson_title or filename).strip() or filename
        week["lessons"].append(
            {
                "lesson_number": int(lesson_number),
                "title": lesson_title_value,
                "filename": filename,
            }
        )

    return {
        "course_slug": course_slug,
        "title": str(course_row[0]),
        "weeks": [weeks_by_number[number] for number in sorted(weeks_by_number)],
    }


def list_courses() -> list[dict]:
    """Return a list of course summary dicts."""
    if SUPABASE_DB_URL:
        try:
            return _list_courses_from_db()
        except Exception as exc:
            _log_db_fallback("list_courses", exc)

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
                "description": _default_description(week_count),
                "week_count": week_count,
                "price_id": None,
                "unit_amount_cents": None,
                "currency": None,
            }
        )
    return results


def get_course_detail(course_slug: str) -> Optional[dict]:
    """Return full course detail dict with weeks and lessons, or None if not found."""
    if SUPABASE_DB_URL:
        try:
            detail = _course_detail_from_db(course_slug)
            if detail is not None:
                return detail
        except Exception as exc:
            _log_db_fallback(f"get_course_detail({course_slug})", exc)

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
