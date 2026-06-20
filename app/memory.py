"""
Phase 2 memory — facts, goals, and timeline events for /chat prompt injection.

Full LLM extraction is post-launch; this module stores and loads structured memory.
"""

from __future__ import annotations

from typing import Optional

from app.config import SUPABASE_DB_URL, logger

_schema_bootstrapped = False

_MAX_FACTS = 5
_MAX_GOALS = 3
_MAX_EVENTS = 5


def _get_db_connection():
    from app.db import db_connection

    return db_connection()


def _ensure_memory_schema() -> bool:
    global _schema_bootstrapped
    if _schema_bootstrapped or not SUPABASE_DB_URL:
        return _schema_bootstrapped

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.user_facts (
                    id BIGSERIAL PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                    fact TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.8,
                    source_message_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.user_goals (
                    id BIGSERIAL PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    progress INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.memory_events (
                    id BIGSERIAL PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    event_summary TEXT NOT NULL,
                    importance SMALLINT NOT NULL DEFAULT 3,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        _schema_bootstrapped = True
        return True
    except Exception as exc:
        logger.error("Failed to bootstrap memory schema: %s", exc)
        return False


def record_memory_event(
    user_id: str,
    event_type: str,
    event_summary: str,
    *,
    importance: int = 3,
) -> None:
    """Append a timeline event (best-effort, non-blocking for chat path)."""
    if not user_id or not SUPABASE_DB_URL:
        return
    if not _ensure_memory_schema():
        return

    summary = event_summary.strip()
    if not summary:
        return

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.memory_events
                    (user_id, event_type, event_summary, importance)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, event_type.strip(), summary, importance),
            )
    except Exception as exc:
        logger.debug("record_memory_event failed user=%s: %s", user_id, exc)


def add_user_fact(user_id: str, fact: str, *, confidence: float = 0.8) -> None:
    if not user_id or not SUPABASE_DB_URL:
        return
    if not _ensure_memory_schema():
        return
    text = fact.strip()
    if not text:
        return
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_facts (user_id, fact, confidence)
                VALUES (%s, %s, %s)
                """,
                (user_id, text, confidence),
            )
    except Exception as exc:
        logger.debug("add_user_fact failed user=%s: %s", user_id, exc)


def format_memory_system_block(user_id: str) -> Optional[str]:
    if not user_id or not SUPABASE_DB_URL:
        return None
    if not _ensure_memory_schema():
        return None

    facts: list[str] = []
    goals: list[str] = []
    events: list[str] = []

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT fact FROM public.user_facts
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, _MAX_FACTS),
            )
            facts = [row[0] for row in cur.fetchall() if row and row[0]]

            cur.execute(
                """
                SELECT title, status, progress FROM public.user_goals
                WHERE user_id = %s AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (user_id, _MAX_GOALS),
            )
            for row in cur.fetchall():
                if row and row[0]:
                    progress = row[2] if row[2] is not None else 0
                    goals.append(f"{row[0]} ({progress}% — {row[1]})")

            cur.execute(
                """
                SELECT event_summary FROM public.memory_events
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, _MAX_EVENTS),
            )
            events = [row[0] for row in cur.fetchall() if row and row[0]]
    except Exception as exc:
        logger.debug("format_memory_system_block failed user=%s: %s", user_id, exc)
        return None

    if not facts and not goals and not events:
        return None

    sections: list[str] = ["[USER MEMORY — structured, not chat history]"]
    if facts:
        sections.append("Known facts:\n- " + "\n- ".join(facts))
    if goals:
        sections.append("Active goals:\n- " + "\n- ".join(goals))
    if events:
        sections.append("Recent events:\n- " + "\n- ".join(events))

    sections.append(
        "Use this memory naturally when relevant. Do not invent facts beyond what is listed."
    )
    return "\n\n".join(sections)


def load_memory_prompt_block(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    return format_memory_system_block(user_id)
