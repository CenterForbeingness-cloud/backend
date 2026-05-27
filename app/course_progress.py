"""
Per-user progress through a daily course schedule (current day number).

Progress is schedule index (Day 1, Day 2, …), not calendar date.
Advance explicitly via advance_day(); no midnight rollover yet.
"""

from __future__ import annotations

from typing import Optional

from app.config import SUPABASE_DB_URL, logger
from app.daily_schedule import (
    build_day_welcome,
    estimate_duration_minutes,
    get_schedule_day,
    validate_course_slug,
)
from app.entitlements import is_product_only_slug

_schema_bootstrapped = False


_max_day_cache: dict[str, Optional[int]] = {}


def clear_max_day_cache(course_slug: Optional[str] = None) -> None:
    if course_slug is None:
        _max_day_cache.clear()
    else:
        _max_day_cache.pop(course_slug, None)


def _get_db_connection():
    from app.db import db_connection

    return db_connection()


def touch_course_activity(user_id: str, course_slug: str) -> None:
    """Update last_activity_at without blocking the chat response path."""
    if not SUPABASE_DB_URL:
        return
    validate_course_slug(course_slug)
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.user_course_progress
                SET last_activity_at = timezone('utc', now())
                WHERE user_id = %s AND course_slug = %s
                """,
                (user_id, course_slug),
            )
    except Exception as exc:
        logger.debug("touch_course_activity failed: %s", exc)


def _ensure_progress_schema() -> None:
    global _schema_bootstrapped
    if _schema_bootstrapped:
        return

    with _get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.user_course_progress (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                course_slug TEXT NOT NULL,
                current_day_number INTEGER NOT NULL DEFAULT 1 CHECK (current_day_number > 0),
                started_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
                last_activity_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
                UNIQUE (user_id, course_slug)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_course_progress_user_course
            ON public.user_course_progress(user_id, course_slug)
            """
        )

    _schema_bootstrapped = True


def get_max_schedule_day(course_slug: str) -> Optional[int]:
    if not SUPABASE_DB_URL:
        return None

    validate_course_slug(course_slug)
    if course_slug in _max_day_cache:
        return _max_day_cache[course_slug]

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(day_number)
                FROM public.course_daily_schedule
                WHERE course_slug = %s
                """,
                (course_slug,),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                _max_day_cache[course_slug] = None
                return None
            value = int(row[0])
            _max_day_cache[course_slug] = value
            return value
    except Exception as exc:
        logger.warning("get_max_schedule_day failed course=%s: %s", course_slug, exc)
        return None


def _clamp_day(day_number: int, max_day: Optional[int]) -> int:
    if max_day is not None and day_number > max_day:
        return max_day
    return day_number


def get_current_day_number(user_id: str, course_slug: str) -> Optional[int]:
    """Return the user's current day, creating progress at day 1 if needed."""
    if not SUPABASE_DB_URL:
        return None

    validate_course_slug(course_slug)
    if is_product_only_slug(course_slug):
        return None
    if get_max_schedule_day(course_slug) is None:
        return None

    _ensure_progress_schema()

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_day_number
                FROM public.user_course_progress
                WHERE user_id = %s AND course_slug = %s
                """,
                (user_id, course_slug),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """
                    INSERT INTO public.user_course_progress
                        (user_id, course_slug, current_day_number)
                    VALUES (%s, %s, 1)
                    RETURNING current_day_number
                    """,
                    (user_id, course_slug),
                )
                row = cur.fetchone()

            day_number = int(row[0])
    except Exception as exc:
        logger.warning(
            "get_current_day_number failed user=%s course=%s: %s",
            user_id,
            course_slug,
            exc,
        )
        return None

    return _clamp_day(day_number, get_max_schedule_day(course_slug))


def resolve_schedule_day_number(
    user_id: Optional[str],
    course_slug: Optional[str],
    explicit_day: Optional[int],
) -> Optional[int]:
    """
    Day to load for chat context.

    explicit_day wins when set; otherwise use stored progress for authenticated users.
    """
    if not course_slug:
        return None

    if explicit_day is not None:
        if explicit_day < 1:
            return None
        return _clamp_day(explicit_day, get_max_schedule_day(course_slug))

    if not user_id:
        return None

    day = get_current_day_number(user_id, course_slug)
    if day is None:
        # Progress row missing or DB error — still run day 1 script if schedule exists
        return 1
    return day


def _progress_payload(
    course_slug: str,
    day_number: int,
    max_day: Optional[int],
) -> dict:
    payload = {
        "course_slug": course_slug,
        "current_day_number": day_number,
        "max_day_number": max_day,
        "day_title": None,
        "duration_minutes": None,
        "welcome_message": None,
    }

    schedule_day = get_schedule_day(course_slug, day_number)
    if not schedule_day:
        return payload

    payload["day_title"] = schedule_day.get("day_title")
    payload["duration_minutes"] = estimate_duration_minutes(schedule_day["content"])
    payload["welcome_message"] = build_day_welcome(schedule_day)
    return payload


def get_progress(user_id: str, course_slug: str) -> Optional[dict]:
    """Return progress row for API responses."""
    day = get_current_day_number(user_id, course_slug)
    if day is None:
        return None

    max_day = get_max_schedule_day(course_slug)
    return _progress_payload(course_slug, day, max_day)


def advance_day(user_id: str, course_slug: str) -> Optional[dict]:
    """Move to the next schedule day if one exists. Idempotent at the last day."""
    if not SUPABASE_DB_URL:
        return None

    validate_course_slug(course_slug)
    _ensure_progress_schema()
    max_day = get_max_schedule_day(course_slug)
    if max_day is None:
        return None

    current = get_current_day_number(user_id, course_slug)
    if current is None:
        return None

    next_day = min(current + 1, max_day)

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.user_course_progress
                SET current_day_number = %s,
                    last_activity_at = timezone('utc', now())
                WHERE user_id = %s AND course_slug = %s
                RETURNING current_day_number
                """,
                (next_day, user_id, course_slug),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _progress_payload(course_slug, int(row[0]), max_day)
    except Exception as exc:
        logger.exception(
            "advance_day failed user=%s course=%s: %s",
            user_id,
            course_slug,
            exc,
        )
        return None
