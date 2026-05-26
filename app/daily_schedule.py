"""
Daily course schedule: parse external text files, persist in Postgres, inject into chat.

Separate from week/lesson catalog and from rag/raw filesystem content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.config import SUPABASE_DB_URL, logger

_SLUG_RE = re.compile(r"^[a-z0-9\-]+$")
_DAY_LINE_RE = re.compile(r"^day\s+(\d+)\s*:\s*(.*)$", re.IGNORECASE)
_DAY_BLOCK_RE = re.compile(
    r"^---\s*day\s+(\d+)(?:\s*:\s*(.*?))?\s*---\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ScheduleDay:
    day_number: int
    day_title: Optional[str]
    content: str


def _get_db_connection():
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")

    import psycopg

    return psycopg.connect(SUPABASE_DB_URL, autocommit=False, connect_timeout=5)


def _split_title_and_content(rest: str) -> tuple[Optional[str], str]:
    if "|" in rest:
        title_part, content_part = rest.split("|", 1)
        title = title_part.strip() or None
        content = content_part.strip()
        return title, content
    return None, rest.strip()


def _block_title_and_content(
    body_lines: list[str],
    inline_title: Optional[str],
) -> tuple[Optional[str], str]:
    if inline_title:
        return inline_title.strip() or None, "\n".join(body_lines).strip()
    if not body_lines:
        return None, ""
    if len(body_lines) == 1:
        return None, body_lines[0].strip()
    return body_lines[0].strip() or None, "\n".join(body_lines[1:]).strip()


def parse_schedule_text(text: str) -> list[ScheduleDay]:
    """
    Parse schedule file content into ordered days.

    Supported formats:
      - Day 1: Title | Content on one line
      - Day 2: Content without title or pipe
      - --- day 3 --- / --- day 3: Title --- multi-line blocks
      - Plain lines (sequential day numbers starting at 1)
    """
    days: list[ScheduleDay] = []
    next_auto_day = 1
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        index += 1

        if not stripped or stripped.startswith("#"):
            continue

        block_match = _DAY_BLOCK_RE.match(stripped)
        if block_match:
            day_number = int(block_match.group(1))
            inline_title = (block_match.group(2) or "").strip() or None
            body_lines: list[str] = []
            while index < len(lines):
                peek = lines[index].strip()
                if _DAY_BLOCK_RE.match(peek) or _DAY_LINE_RE.match(peek):
                    break
                if peek or body_lines:
                    body_lines.append(lines[index].rstrip())
                index += 1
            title, content = _block_title_and_content(body_lines, inline_title)
            if content:
                days.append(ScheduleDay(day_number, title, content))
            continue

        day_match = _DAY_LINE_RE.match(stripped)
        if day_match:
            day_number = int(day_match.group(1))
            title, content = _split_title_and_content(day_match.group(2))
            if content:
                days.append(ScheduleDay(day_number, title, content))
            continue

        days.append(ScheduleDay(next_auto_day, None, stripped))
        next_auto_day += 1

    return days


def validate_course_slug(course_slug: str) -> None:
    if not _SLUG_RE.match(course_slug):
        raise ValueError(f"Invalid course_slug: {course_slug!r}")


def replace_schedule(course_slug: str, days: list[ScheduleDay]) -> int:
    """Replace all schedule rows for a course. Returns number of days written."""
    validate_course_slug(course_slug)
    if not days:
        raise ValueError("Schedule file produced no days")

    conn = _get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM public.courses WHERE course_slug = %s",
                (course_slug,),
            )
            if cur.fetchone() is None:
                raise ValueError(f"Unknown course_slug (not in courses table): {course_slug}")

            cur.execute(
                "DELETE FROM public.course_daily_schedule WHERE course_slug = %s",
                (course_slug,),
            )
            for day in days:
                cur.execute(
                    """
                    INSERT INTO public.course_daily_schedule
                        (course_slug, day_number, day_title, content)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (course_slug, day.day_number, day.day_title, day.content),
                )
        conn.commit()
        return len(days)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_schedule_day(course_slug: str, day_number: int) -> Optional[dict]:
    """Load one schedule day from the database, or None if missing / DB unavailable."""
    if not SUPABASE_DB_URL or day_number < 1:
        return None

    validate_course_slug(course_slug)
    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT day_number, day_title, content
                    FROM public.course_daily_schedule
                    WHERE course_slug = %s AND day_number = %s
                    """,
                    (course_slug, day_number),
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "get_schedule_day failed course=%s day=%s: %s",
            course_slug,
            day_number,
            exc,
        )
        return None

    if not row:
        return None
    return {
        "day_number": row[0],
        "day_title": row[1],
        "content": row[2],
    }


def format_day_context(day: dict) -> str:
    header = f"Day {day['day_number']}"
    if day.get("day_title"):
        header = f"{header}: {day['day_title']}"
    return f"[Course schedule — {header}]\n{day['content']}"


def estimate_duration_minutes(content: str) -> int:
    """Rough guided-practice length from schedule copy (spoken pacing)."""
    words = len(content.split())
    minutes = max(2, round(words / 90))
    return min(minutes, 45)


def build_day_welcome(day: dict) -> str:
    """
    First assistant message before the user speaks — same shape every day.
    """
    day_number = day["day_number"]
    day_title = day.get("day_title")
    duration = estimate_duration_minutes(day["content"])

    headline = f"Day {day_number}"
    if day_title:
        headline = f"{headline}: {day_title}"

    duration_label = f"{duration} minute{'s' if duration != 1 else ''}"

    return (
        f"Welcome to {headline}.\n\n"
        f"Today's practice is about {duration_label}.\n\n"
        f"When you're ready, tell me how you're feeling or ask to begin."
    )
