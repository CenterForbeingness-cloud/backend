"""
analytics.py — MVP Launch event tracking (Postgres analytics_events).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.config import SUPABASE_DB_URL, logger
from app.rag import RetrievalResult

_schema_bootstrapped = False


def _get_db_connection():
    from app.db import db_connection

    return db_connection()


def _ensure_analytics_schema() -> bool:
    global _schema_bootstrapped
    if _schema_bootstrapped or not SUPABASE_DB_URL:
        return _schema_bootstrapped

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.analytics_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
                    event_name TEXT NOT NULL,
                    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_analytics_events_name_created
                ON public.analytics_events (event_name, created_at DESC)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_analytics_events_user_created
                ON public.analytics_events (user_id, created_at DESC)
                """
            )
        _schema_bootstrapped = True
        return True
    except Exception as exc:
        logger.error("Failed to bootstrap analytics schema: %s", exc)
        return False


def track(
    user_id: Optional[str],
    event_name: str,
    **properties: Any,
) -> None:
    """Best-effort insert; never raises to callers."""
    if not SUPABASE_DB_URL:
        return
    if not _ensure_analytics_schema():
        return

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.analytics_events (user_id, event_name, properties)
                VALUES (%s::uuid, %s, %s::jsonb)
                """,
                (user_id, event_name, json.dumps(properties)),
            )
    except Exception as exc:
        logger.warning("analytics track failed event=%s: %s", event_name, exc)


def track_rag_retrieval(
    user_id: Optional[str],
    retrieval: RetrievalResult,
    *,
    course_slug: Optional[str] = None,
    session_mode: str = "text",
) -> None:
    """Record RAG provenance without re-querying Pinecone."""
    hits = retrieval.retrievals
    props: dict[str, Any] = {
        "rag_hit": retrieval.rag_hit,
        "top_score": retrieval.top_score,
        "course_slug": course_slug,
        "session_mode": session_mode,
        "hit_count": len(hits),
    }
    if hits:
        props["source_types"] = sorted({h.source_type for h in hits})
        props["lessons"] = sorted({h.lesson for h in hits if h.lesson})
        props["retrieval_ids"] = [h.id for h in hits]

    event = "rag_retrieval" if retrieval.rag_hit else "rag_retrieval_miss"
    track(user_id, event, **props)


def track_chat_message(
    user_id: Optional[str],
    *,
    session_mode: str = "text",
    course_slug: Optional[str] = None,
    scripted: bool = False,
) -> None:
    track(
        user_id,
        "chat_message_sent",
        session_mode=session_mode,
        course_slug=course_slug,
        scripted=scripted,
    )


def track_purchase_completed(
    user_id: str,
    course_slug: str,
    *,
    amount_cents: Optional[int] = None,
    stripe_event_id: Optional[str] = None,
) -> None:
    track(
        user_id,
        "purchase_completed",
        course_slug=course_slug,
        amount_cents=amount_cents,
        stripe_event_id=stripe_event_id,
    )
