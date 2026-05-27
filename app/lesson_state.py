"""
Per chat session: which beat index we are on for a daily lesson.
"""

from __future__ import annotations

from typing import Optional

from app.config import SUPABASE_DB_URL, logger
from app.daily_schedule import validate_course_slug

_schema_ready = False


def _connection():
    from app.db import db_connection

    return db_connection()


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.lesson_session_state (
                session_id TEXT NOT NULL,
                course_slug TEXT NOT NULL,
                day_number INTEGER NOT NULL,
                beat_index INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
                PRIMARY KEY (session_id, course_slug, day_number)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lesson_session_state_session
            ON public.lesson_session_state(session_id)
            """
        )
    _schema_ready = True


def get_beat_index(session_id: str, course_slug: str, day_number: int) -> int:
    if not SUPABASE_DB_URL:
        return 0
    validate_course_slug(course_slug)
    _ensure_schema()
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT beat_index
                FROM public.lesson_session_state
                WHERE session_id = %s AND course_slug = %s AND day_number = %s
                """,
                (session_id, course_slug, day_number),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning("get_beat_index failed: %s", exc)
        return 0


def set_beat_index(
    session_id: str, course_slug: str, day_number: int, beat_index: int
) -> None:
    if not SUPABASE_DB_URL:
        return
    validate_course_slug(course_slug)
    _ensure_schema()
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.lesson_session_state
                    (session_id, course_slug, day_number, beat_index, updated_at)
                VALUES (%s, %s, %s, %s, timezone('utc', now()))
                ON CONFLICT (session_id, course_slug, day_number)
                DO UPDATE SET
                    beat_index = EXCLUDED.beat_index,
                    updated_at = EXCLUDED.updated_at
                """,
                (session_id, course_slug, day_number, beat_index),
            )
    except Exception as exc:
        logger.warning("set_beat_index failed: %s", exc)


def reset_lesson_state(
    session_id: str,
    course_slug: Optional[str] = None,
) -> None:
    if not SUPABASE_DB_URL:
        return
    _ensure_schema()
    try:
        with _connection() as conn, conn.cursor() as cur:
            if course_slug:
                cur.execute(
                    """
                    DELETE FROM public.lesson_session_state
                    WHERE session_id = %s AND course_slug = %s
                    """,
                    (session_id, course_slug),
                )
            else:
                cur.execute(
                    "DELETE FROM public.lesson_session_state WHERE session_id = %s",
                    (session_id,),
                )
    except Exception as exc:
        logger.warning("reset_lesson_state failed: %s", exc)
