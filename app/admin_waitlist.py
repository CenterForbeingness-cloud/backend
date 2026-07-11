"""
admin_waitlist.py — Website waitlist read API for admin (Phase 1 integration).

Source of truth: public.waitlist_signups (written by Sentaint Web). Same Supabase DB as backend.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import SUPABASE_DB_URL, logger
from app.db import db_connection
from app.models import (
    AdminWaitlistEntry,
    AdminWaitlistListResponse,
    AdminWaitlistStatsResponse,
)

_schema_bootstrapped = False


def ensure_waitlist_schema() -> bool:
    """Create waitlist_signups if missing (dev convenience; prod should run SQL migration)."""
    global _schema_bootstrapped
    if _schema_bootstrapped or not SUPABASE_DB_URL:
        return _schema_bootstrapped

    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.waitlist_signups (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  email text NOT NULL,
                  source text NOT NULL DEFAULT 'sentient-landing',
                  created_at timestamptz NOT NULL DEFAULT now(),
                  launch_notified_at timestamptz,
                  ip_hash text,
                  CONSTRAINT waitlist_signups_email_key UNIQUE (email)
                )
                """
            )
        _schema_bootstrapped = True
    except Exception as exc:
        logger.warning("waitlist schema bootstrap failed: %s", exc)
    return _schema_bootstrapped


def waitlist_table_available() -> bool:
    if not SUPABASE_DB_URL:
        return False
    ensure_waitlist_schema()
    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'waitlist_signups'
                LIMIT 1
                """
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def _row_to_entry(row: tuple[Any, ...]) -> AdminWaitlistEntry:
    return AdminWaitlistEntry(
        id=str(row[0]),
        email=row[1],
        source=row[2] or "sentient-landing",
        created_at=row[3],
        launch_notified_at=row[4],
    )


def list_waitlist_signups(
    *,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    pending_only: bool = False,
) -> AdminWaitlistListResponse:
    if not SUPABASE_DB_URL:
        raise RuntimeError("Database not configured")

    ensure_waitlist_schema()
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    q = query.strip().lower()

    conditions = ["1=1"]
    params: list[Any] = []
    if pending_only:
        conditions.append("launch_notified_at IS NULL")
    if q:
        conditions.append("lower(email) LIKE %s")
        params.append(f"%{q}%")

    where_sql = " AND ".join(conditions)

    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT lower(trim(email)))
                FROM public.waitlist_signups
                WHERE {where_sql}
                """,
                tuple(params),
            )
            total = int(cur.fetchone()[0])

            cur.execute(
                f"""
                SELECT DISTINCT ON (lower(trim(email)))
                  id, email, source, created_at, launch_notified_at
                FROM public.waitlist_signups
                WHERE {where_sql}
                ORDER BY lower(trim(email)), created_at DESC
                LIMIT %s OFFSET %s
                """,
                (*params, limit, offset),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.exception("list_waitlist_signups failed: %s", exc)
        raise RuntimeError("Waitlist read failed") from exc

    return AdminWaitlistListResponse(
        total=total,
        limit=limit,
        offset=offset,
        signups=[_row_to_entry(r) for r in rows],
    )


def get_waitlist_stats() -> AdminWaitlistStatsResponse:
    if not SUPABASE_DB_URL:
        raise RuntimeError("Database not configured")

    ensure_waitlist_schema()
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)

    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*) AS total_rows,
                  COUNT(DISTINCT lower(trim(email))) AS distinct_emails
                FROM public.waitlist_signups
                """
            )
            row = cur.fetchone()
            total_rows = int(row[0])
            distinct_emails = int(row[1])

            cur.execute(
                """
                SELECT COUNT(DISTINCT lower(trim(email)))
                FROM public.waitlist_signups
                WHERE created_at >= %s
                """,
                (week_start,),
            )
            this_week = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT COUNT(DISTINCT lower(trim(email)))
                FROM public.waitlist_signups
                WHERE launch_notified_at IS NULL
                """
            )
            pending_launch = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT COUNT(DISTINCT lower(trim(email)))
                FROM public.waitlist_signups
                WHERE launch_notified_at IS NOT NULL
                """
            )
            launch_notified = int(cur.fetchone()[0])
    except Exception as exc:
        logger.exception("get_waitlist_stats failed: %s", exc)
        raise RuntimeError("Waitlist stats failed") from exc

    duplicate_rows = max(0, total_rows - distinct_emails)
    return AdminWaitlistStatsResponse(
        total_signups=distinct_emails,
        signups_this_week=this_week,
        pending_launch_notify=pending_launch,
        launch_notified_count=launch_notified,
        total_rows=total_rows,
        distinct_emails=distinct_emails,
        duplicate_row_count=duplicate_rows,
        email_integrity_ok=duplicate_rows == 0,
        generated_at=now,
    )


def export_waitlist_csv(*, query: str = "", pending_only: bool = False) -> str:
    """One row per normalized email (latest signup wins)."""
    chunk = list_waitlist_signups(
        query=query,
        limit=10_000,
        offset=0,
        pending_only=pending_only,
    )
    lines = ["email,source,created_at,launch_notified_at"]
    for s in chunk.signups:
        created = s.created_at.isoformat() if s.created_at else ""
        notified = s.launch_notified_at.isoformat() if s.launch_notified_at else ""
        email = s.email.replace('"', '""')
        source = (s.source or "").replace('"', '""')
        lines.append(f'"{email}","{source}","{created}","{notified}"')
    return "\n".join(lines) + "\n"
