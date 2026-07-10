"""
marketing_traffic.py — Phase 2 page-view beacon ingest and daily rollups.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config import SUPABASE_DB_URL, logger
from app.db import db_connection
from app.models import AdminWaitlistTrafficDay, AdminWaitlistTrafficResponse

_schema_bootstrapped = False
_PATH_RE = re.compile(r"^[a-zA-Z0-9/_\-.\~]{0,200}$")


def ensure_marketing_traffic_schema() -> bool:
    global _schema_bootstrapped
    if _schema_bootstrapped or not SUPABASE_DB_URL:
        return _schema_bootstrapped
    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.marketing_beacon_dedup (
                  stat_date date NOT NULL,
                  page_path text NOT NULL DEFAULT '/',
                  session_id text NOT NULL,
                  created_at timestamptz NOT NULL DEFAULT now(),
                  PRIMARY KEY (stat_date, page_path, session_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.marketing_daily_rollups (
                  stat_date date NOT NULL,
                  page_path text NOT NULL DEFAULT '/',
                  page_views integer NOT NULL DEFAULT 0,
                  unique_sessions integer NOT NULL DEFAULT 0,
                  PRIMARY KEY (stat_date, page_path)
                )
                """
            )
        _schema_bootstrapped = True
    except Exception as exc:
        logger.warning("marketing traffic schema bootstrap failed: %s", exc)
    return _schema_bootstrapped


def marketing_traffic_available() -> bool:
    if not SUPABASE_DB_URL:
        return False
    ensure_marketing_traffic_schema()
    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'marketing_daily_rollups'
                LIMIT 1
                """
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def normalize_page_path(path: str | None) -> str:
    raw = (path or "/").strip()
    if not raw or raw == "":
        return "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    if "?" in raw:
        raw = raw.split("?", 1)[0]
    if len(raw) > 200:
        raw = raw[:200]
    if not _PATH_RE.match(raw):
        return "/"
    return raw


def normalize_session_id(session_id: str | None) -> str:
    sid = (session_id or "").strip()
    if len(sid) < 8 or len(sid) > 64:
        return ""
    if not re.match(r"^[a-zA-Z0-9\-_]+$", sid):
        return ""
    return sid


def record_page_view(
    *,
    path: str | None,
    session_id: str | None,
    referrer: str | None = None,
) -> tuple[bool, str]:
    """
    Record one page view. Returns (counted, reason).
    counted=False when session/path/day already recorded (deduped).
    """
    if not SUPABASE_DB_URL:
        raise RuntimeError("Database not configured")

    ensure_marketing_traffic_schema()
    page_path = normalize_page_path(path)
    sid = normalize_session_id(session_id)
    if not sid:
        return False, "invalid_session_id"

    stat_date = datetime.now(timezone.utc).date()
    ref = (referrer or "").strip()[:500] if referrer else None
    if ref is not None and not ref:
        ref = None

    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.marketing_beacon_dedup (stat_date, page_path, session_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING session_id
                """,
                (stat_date, page_path, sid),
            )
            inserted = cur.fetchone() is not None
            if not inserted:
                return False, "duplicate_session"

            cur.execute(
                """
                INSERT INTO public.marketing_daily_rollups (stat_date, page_path, page_views, unique_sessions)
                VALUES (%s, %s, 1, 1)
                ON CONFLICT (stat_date, page_path)
                DO UPDATE SET
                  page_views = marketing_daily_rollups.page_views + 1,
                  unique_sessions = marketing_daily_rollups.unique_sessions + 1
                """,
                (stat_date, page_path),
            )
    except Exception as exc:
        logger.exception("record_page_view failed: %s", exc)
        raise RuntimeError("Page view record failed") from exc

    return True, "ok"


def get_waitlist_traffic(*, days: int = 7, page_path: str | None = None) -> AdminWaitlistTrafficResponse:
    if not SUPABASE_DB_URL:
        raise RuntimeError("Database not configured")

    ensure_marketing_traffic_schema()
    days = max(1, min(days, 90))
    path_filter = normalize_page_path(page_path) if page_path else None
    start = datetime.now(timezone.utc).date() - timedelta(days=days - 1)

    try:
        with db_connection() as conn, conn.cursor() as cur:
            if path_filter:
                cur.execute(
                    """
                    SELECT stat_date, page_views, unique_sessions
                    FROM public.marketing_daily_rollups
                    WHERE stat_date >= %s AND page_path = %s
                    ORDER BY stat_date ASC
                    """,
                    (start, path_filter),
                )
            else:
                cur.execute(
                    """
                    SELECT stat_date, SUM(page_views), SUM(unique_sessions)
                    FROM public.marketing_daily_rollups
                    WHERE stat_date >= %s
                    GROUP BY stat_date
                    ORDER BY stat_date ASC
                    """,
                    (start,),
                )
            traffic_rows = cur.fetchall()

            cur.execute(
                """
                SELECT (created_at AT TIME ZONE 'UTC')::date AS d, COUNT(DISTINCT lower(trim(email)))
                FROM public.waitlist_signups
                WHERE created_at >= %s
                GROUP BY d
                """,
                (datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),),
            )
            signup_by_day = {row[0]: int(row[1]) for row in cur.fetchall()}
    except Exception as exc:
        logger.exception("get_waitlist_traffic failed: %s", exc)
        raise RuntimeError("Traffic read failed") from exc

    traffic_map: dict[date, tuple[int, int]] = {}
    for row in traffic_rows:
        d = row[0]
        traffic_map[d] = (int(row[1] or 0), int(row[2] or 0))

    day_list: list[AdminWaitlistTrafficDay] = []
    total_views = 0
    total_sessions = 0
    total_signups = 0

    for i in range(days):
        d = start + timedelta(days=i)
        views, sessions = traffic_map.get(d, (0, 0))
        signups = signup_by_day.get(d, 0)
        conv = round(100.0 * signups / sessions, 2) if sessions > 0 else None
        day_list.append(
            AdminWaitlistTrafficDay(
                stat_date=d,
                page_views=views,
                unique_sessions=sessions,
                signups=signups,
                conversion_pct=conv,
            )
        )
        total_views += views
        total_sessions += sessions
        total_signups += signups

    period_conv = (
        round(100.0 * total_signups / total_sessions, 2) if total_sessions > 0 else None
    )

    return AdminWaitlistTrafficResponse(
        period_days=days,
        page_path=path_filter or "(all pages)",
        generated_at=datetime.now(timezone.utc),
        days=day_list,
        total_page_views=total_views,
        total_unique_sessions=total_sessions,
        total_signups=total_signups,
        period_conversion_pct=period_conv,
    )
