"""
Per-user progress through a daily course schedule (current day number).

Progress is schedule index (Day 1, Day 2, …), not calendar date.
Advance explicitly via advance_day(); no midnight rollover yet.
"""

from __future__ import annotations

from typing import Optional

from app.config import SUPABASE_DB_URL, logger
from app.daily_schedule import validate_course_slug

_schema_bootstrapped = False


def _get_db_connection():
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")

    import psycopg

    return psycopg.connect(SUPABASE_DB_URL, autocommit=True, connect_timeout=5)


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
                return None
            return int(row[0])
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
            else:
                cur.execute(
                    """
                    UPDATE public.user_course_progress
                    SET last_activity_at = timezone('utc', now())
                    WHERE user_id = %s AND course_slug = %s
                    """,
                    (user_id, course_slug),
                )

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

    return get_current_day_number(user_id, course_slug)


def get_progress(user_id: str, course_slug: str) -> Optional[dict]:
    """Return progress row for API responses."""
    day = get_current_day_number(user_id, course_slug)
    if day is None:
        return None

    max_day = get_max_schedule_day(course_slug)
    return {
        "course_slug": course_slug,
        "current_day_number": day,
        "max_day_number": max_day,
    }


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
            return {
                "course_slug": course_slug,
                "current_day_number": int(row[0]),
                "max_day_number": max_day,
            }
    except Exception as exc:
        logger.exception(
            "advance_day failed user=%s course=%s: %s",
            user_id,
            course_slug,
            exc,
        )
        return None
