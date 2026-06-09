"""
admin_ops.py — Phase 3 admin ops: analytics summary, quota pressure, schedule health.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import FAIR_USE_LIMIT, QUOTA_RESET_PERIOD_HOURS, SUPABASE_DB_URL, VOICE_DAILY_SECONDS_CAP, logger
from app.daily_schedule import validate_course_slug
from app.models import (
    AdminAnalyticsSummaryResponse,
    AdminEventCount,
    AdminQuotaPressureUser,
    AdminRagHealthSnippet,
    AdminScheduleDayRow,
    AdminScheduleHealthResponse,
    AdminVoiceHealthSnippet,
)


def _table_exists(cur: Any, table_name: str) -> bool:
    try:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            LIMIT 1
            """,
            (table_name,),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _count_since(cur: Any, sql: str, since: datetime, *params: Any) -> int:
    cur.execute(sql, (since, *params))
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def get_admin_analytics_summary(days: int = 7) -> AdminAnalyticsSummaryResponse:
    """Roll up product + AI health signals for the admin Ops tab."""
    if not SUPABASE_DB_URL:
        raise RuntimeError("Database not configured")

    period_days = max(1, min(days, 90))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=period_days)
    tables: dict[str, bool] = {}
    event_counts: list[AdminEventCount] = []
    new_users = 0
    profiles_with_goals = 0
    purchases_completed = 0
    chat_messages_db = 0
    rag = AdminRagHealthSnippet(hits=0, misses=0, miss_rate_pct=0.0)
    voice = AdminVoiceHealthSnippet(
        voice_sessions=0,
        spoken_seconds_total=0.0,
        users_near_voice_cap=0,
    )
    quota_pressure: list[AdminQuotaPressureUser] = []
    threshold = max(1, int(FAIR_USE_LIMIT * 0.8))

    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            for name in (
                "analytics_events",
                "user_message_counts",
                "user_profile",
                "chat_messages",
                "user_voice_usage",
            ):
                tables[name] = _table_exists(cur, name)
            try:
                cur.execute("SELECT 1 FROM auth.users LIMIT 1")
                tables["auth.users"] = True
            except Exception:
                tables["auth.users"] = False

            if tables.get("auth.users"):
                try:
                    new_users = _count_since(
                        cur,
                        "SELECT COUNT(*) FROM auth.users WHERE created_at >= %s",
                        since,
                    )
                except Exception as exc:
                    logger.warning("admin summary new_users: %s", exc)

            if tables.get("user_profile"):
                try:
                    profiles_with_goals = _count_since(
                        cur,
                        """
                        SELECT COUNT(*) FROM public.user_profile
                        WHERE updated_at >= %s
                          AND (
                            NULLIF(TRIM(primary_goal), '') IS NOT NULL
                            OR NULLIF(TRIM(current_focus), '') IS NOT NULL
                          )
                        """,
                        since,
                    )
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM public.user_profile
                        WHERE NULLIF(TRIM(primary_goal), '') IS NOT NULL
                           OR NULLIF(TRIM(current_focus), '') IS NOT NULL
                        """
                    )
                    row = cur.fetchone()
                    profiles_with_goals_total = int(row[0] or 0) if row else 0
                except Exception as exc:
                    logger.warning("admin summary profiles: %s", exc)
                    profiles_with_goals_total = 0
            else:
                profiles_with_goals_total = 0

            if tables.get("analytics_events"):
                try:
                    cur.execute(
                        """
                        SELECT event_name, COUNT(*)::int
                        FROM public.analytics_events
                        WHERE created_at >= %s
                        GROUP BY event_name
                        ORDER BY COUNT(*) DESC
                        LIMIT 30
                        """,
                        (since,),
                    )
                    event_counts = [
                        AdminEventCount(event_name=str(row[0]), count=int(row[1]))
                        for row in cur.fetchall()
                    ]
                    hits = next((e.count for e in event_counts if e.event_name == "rag_retrieval"), 0)
                    misses = next(
                        (e.count for e in event_counts if e.event_name == "rag_retrieval_miss"), 0
                    )
                    total_rag = hits + misses
                    miss_rate = (misses / total_rag * 100.0) if total_rag else 0.0
                    rag = AdminRagHealthSnippet(
                        hits=hits,
                        misses=misses,
                        miss_rate_pct=round(miss_rate, 1),
                    )
                    purchases_completed = next(
                        (e.count for e in event_counts if e.event_name == "purchase_completed"), 0
                    )
                    voice_sessions = next(
                        (e.count for e in event_counts if e.event_name == "voice_session_end"), 0
                    )
                    spoken_total = 0.0
                    cur.execute(
                        """
                        SELECT COALESCE(SUM((properties->>'spoken_seconds')::numeric), 0)
                        FROM public.analytics_events
                        WHERE created_at >= %s AND event_name = 'voice_session_end'
                        """,
                        (since,),
                    )
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        spoken_total = float(row[0])
                    voice = AdminVoiceHealthSnippet(
                        voice_sessions=voice_sessions,
                        spoken_seconds_total=round(spoken_total, 1),
                        users_near_voice_cap=0,
                    )
                except Exception as exc:
                    logger.warning("admin summary analytics_events: %s", exc)

            if tables.get("chat_messages"):
                try:
                    chat_messages_db = _count_since(
                        cur,
                        "SELECT COUNT(*) FROM public.chat_messages WHERE created_at >= %s",
                        since,
                    )
                except Exception as exc:
                    logger.warning("admin summary chat_messages: %s", exc)

            if tables.get("user_message_counts"):
                try:
                    cur.execute(
                        """
                        SELECT m.user_id, u.email, m.message_count
                        FROM public.user_message_counts m
                        LEFT JOIN auth.users u ON u.id = m.user_id
                        WHERE m.message_count >= %s
                          AND m.period_start > %s
                        ORDER BY m.message_count DESC
                        LIMIT 25
                        """,
                        (
                            threshold,
                            now - timedelta(hours=QUOTA_RESET_PERIOD_HOURS),
                        ),
                    )
                    for row in cur.fetchall():
                        count = int(row[2] or 0)
                        quota_pressure.append(
                            AdminQuotaPressureUser(
                                user_id=str(row[0]),
                                email=row[1],
                                messages_today=count,
                                limit=FAIR_USE_LIMIT,
                                pct_used=round(count / FAIR_USE_LIMIT * 100.0, 1)
                                if FAIR_USE_LIMIT
                                else 0.0,
                            )
                        )
                except Exception as exc:
                    logger.warning("admin summary quota_pressure: %s", exc)

            if tables.get("user_voice_usage"):
                try:
                    voice_cap_threshold = max(1, int(VOICE_DAILY_SECONDS_CAP * 0.8))
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM public.user_voice_usage
                        WHERE voice_seconds_today >= %s
                          AND period_start > %s
                        """,
                        (
                            voice_cap_threshold,
                            now - timedelta(hours=QUOTA_RESET_PERIOD_HOURS),
                        ),
                    )
                    row = cur.fetchone()
                    voice = AdminVoiceHealthSnippet(
                        voice_sessions=voice.voice_sessions,
                        spoken_seconds_total=voice.spoken_seconds_total,
                        users_near_voice_cap=int(row[0] or 0) if row else 0,
                    )
                except Exception as exc:
                    logger.warning("admin summary voice_usage: %s", exc)

    except Exception as exc:
        logger.exception("get_admin_analytics_summary failed: %s", exc)
        raise

    return AdminAnalyticsSummaryResponse(
        period_days=period_days,
        generated_at=now,
        new_users=new_users,
        profiles_with_goals_period=profiles_with_goals,
        profiles_with_goals_total=profiles_with_goals_total,
        event_counts=event_counts,
        rag_health=rag,
        voice_health=voice,
        purchases_completed=purchases_completed,
        chat_messages_period=chat_messages_db,
        quota_pressure=quota_pressure,
        fair_use_limit=FAIR_USE_LIMIT,
        tables_available=tables,
    )


def get_admin_schedule_health(course_slug: str) -> AdminScheduleHealthResponse:
    """Day count and titles for schedule import verification."""
    if not SUPABASE_DB_URL:
        raise RuntimeError("Database not configured")

    slug = validate_course_slug(course_slug)
    days: list[AdminScheduleDayRow] = []

    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT day_number, day_title, content
                FROM public.course_daily_schedule
                WHERE course_slug = %s
                ORDER BY day_number ASC
                """,
                (slug,),
            )
            for row in cur.fetchall():
                content = (row[2] or "").strip()
                preview = content[:120] + ("…" if len(content) > 120 else "")
                days.append(
                    AdminScheduleDayRow(
                        day_number=int(row[0]),
                        day_title=row[1],
                        content_preview=preview or "—",
                    )
                )
    except Exception as exc:
        logger.exception("get_admin_schedule_health failed slug=%s: %s", slug, exc)
        raise

    return AdminScheduleHealthResponse(
        course_slug=slug,
        day_count=len(days),
        days=days,
    )
