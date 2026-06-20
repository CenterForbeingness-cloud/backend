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


_schedule_day_cache: dict[tuple[str, int], dict] = {}


def clear_schedule_cache(course_slug: Optional[str] = None) -> None:
    """Drop cached schedule rows after import or in tests."""
    if course_slug is None:
        _schedule_day_cache.clear()
        return
    for key in list(_schedule_day_cache):
        if key[0] == course_slug:
            del _schedule_day_cache[key]


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
        clear_schedule_cache(course_slug)
        from app.course_progress import clear_max_day_cache
        from app.lesson_script import clear_lesson_beats_cache

        clear_max_day_cache(course_slug)
        clear_lesson_beats_cache()
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
    cache_key = (course_slug, day_number)
    if cache_key in _schedule_day_cache:
        return _schedule_day_cache[cache_key]

    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT day_number, day_title, content
                FROM public.course_daily_schedule
                WHERE course_slug = %s AND day_number = %s
                """,
                (course_slug, day_number),
            )
            row = cur.fetchone()
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
    payload = {
        "day_number": row[0],
        "day_title": row[1],
        "content": row[2],
    }
    _schedule_day_cache[cache_key] = payload
    return payload


def format_day_context(day: dict) -> str:
    """Legacy context block; prefer build_schedule_system_block for chat prompts."""
    header = f"Day {day['day_number']}"
    if day.get("day_title"):
        header = f"{header}: {day['day_title']}"
    return f"[Course schedule — {header}]\n{day['content']}"


_SCHEDULE_COACHING_RULES = """\
[Daily lesson — STRICT SCRIPT MODE]
The app already showed today's welcome. Coach ONLY through the numbered lesson script below.

Non-negotiable rules:
1. Deliver ONE script step per message (split a step across messages only if the step itself has multiple lines, e.g. three breaths).
2. Keep replies short: about two to four sentences unless the step gives longer guided text to read aloud.
3. Greetings (hello, hi, hey, good morning, etc.) are NOT small talk — immediately deliver Step 1. Never reply with "Hello! How can I help?" or open chat.
4. Affirmatives (ready, yes, ok, okay, sure, begin, start, continue, next) mean: deliver the NEXT step you have not yet given in this conversation.
5. Off-topic messages: one calm redirect sentence, then repeat the current step's instruction. Do not engage the tangent.
6. Never invent steps, techniques, or timings not in the script. Never teach a different day's lesson.
7. Do not repeat the app welcome or say "Welcome to Day N" unless they explicitly ask what day it is.
8. Track which step you last delivered; do not skip ahead unless the script says to move on after their answer or "ready".
9. When the script's closing step is done, remind them they can tap Complete day in the app.
"""


def build_schedule_system_block(day: dict) -> str:
    """System-prompt block for /chat when a daily schedule day is active (strict script mode)."""
    day_number = day["day_number"]
    title = (day.get("day_title") or "Practice").strip()
    content = day["content"].strip()
    duration = estimate_duration_minutes(content)

    return (
        f"{_SCHEDULE_COACHING_RULES}\n"
        f"[TODAY'S LESSON — Day {day_number}: {title}]\n"
        f"(About {duration} minutes of guided practice.)\n\n"
        f"{content}"
    )


_SCHEDULE_GUIDE_RULES = """\
[Daily companion — today's focus]
The admin notes below describe themes for this schedule day. They are guidance for you, not lines to read aloud.

Rules:
1. Coach in your own words — warm, concise, and responsive to what the user just said.
2. Use [Additional context] from the course (RAG) when it helps teach today's themes.
3. Personalize with the user's profile (goals, habits, patterns) when available.
4. Stay on today's themes; do not jump ahead to a later day.
5. Offer a short guided practice (~5–15 minutes) unless they ask for something else.
6. When the session feels complete, remind them they can tap **Complete day** in the app.
7. Never paste the admin notes verbatim or announce rigid step numbers unless the user asks for structure.
"""


def build_schedule_guide_block(day: dict) -> str:
    """System-prompt block for guide-mode daily practice (themes + RAG, not verbatim script)."""
    day_number = day["day_number"]
    title = (day.get("day_title") or "Practice").strip()
    content = day["content"].strip()
    duration = estimate_duration_minutes(content)

    return (
        f"{_SCHEDULE_GUIDE_RULES}\n"
        f"[TODAY'S FOCUS — Day {day_number}: {title}]\n"
        f"(Roughly {duration} minutes if they want a full sit.)\n\n"
        f"{content}"
    )


def build_schedule_context_block(day: dict, *, guide_mode: bool) -> str:
    if guide_mode:
        return build_schedule_guide_block(day)
    return build_schedule_system_block(day)


def estimate_duration_minutes(content: str) -> int:
    """Rough guided-practice length from schedule copy (spoken pacing)."""
    words = len(content.split())
    minutes = max(2, round(words / 90))
    return min(minutes, 45)


def build_day_welcome(day: dict, *, guide_mode: bool = False) -> str:
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

    if guide_mode:
        return (
            f"Welcome to {headline}.\n\n"
            f"Today's focus is about {duration_label}. "
            f"Tell me how you're arriving, or what you'd like from this session — "
            f"I'll guide you from there."
        )

    return (
        f"Welcome to {headline}.\n\n"
        f"Today's practice is about {duration_label}. "
        f"We'll follow a fixed step-by-step script.\n\n"
        f"Reply **ready** to begin, or say **hello** — we'll start at Step 1."
    )
