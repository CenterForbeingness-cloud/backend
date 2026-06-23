#!/usr/bin/env python3
"""
Gate 4 ops report — funnel counts, voice engagement, purchases.

Usage (from backend/, venv active):
  python scripts/analytics_weekly_report.py
  python scripts/analytics_weekly_report.py --days 7
  python scripts/analytics_weekly_report.py --days 30 --csv report.csv

Requires SUPABASE_DB_URL in backend/.env (service Postgres URL).
Revenue: reconcile totals with Stripe Dashboard (test or live).
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.config import SUPABASE_DB_URL  # noqa: E402

FUNNEL_EVENTS = (
    "signup_complete",
    "first_chat_message",
    "voice_session_start",
    "checkout_started",
    "purchase_completed",
)


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, days))


def _scalar(cur, sql: str, since: datetime, *params) -> int | float:
    cur.execute(sql, (since, *params))
    row = cur.fetchone()
    if not row or row[0] is None:
        return 0
    return row[0]


def run_report(days: int) -> dict:
    if not SUPABASE_DB_URL:
        raise SystemExit("SUPABASE_DB_URL is not set in backend/.env")

    since = _since(days)
    from app.db import db_connection

    out: dict = {"period_days": days, "since": since.isoformat()}

    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'analytics_events'
            LIMIT 1
            """
        )
        if not cur.fetchone():
            raise SystemExit(
                "analytics_events table missing. Run backend/sql/supabase_analytics_events.sql"
            )

        funnel: dict[str, int] = {}
        for name in FUNNEL_EVENTS:
            funnel[name] = int(
                _scalar(
                    cur,
                    """
                    SELECT COUNT(DISTINCT user_id)
                    FROM public.analytics_events
                    WHERE event_name = %s AND created_at >= %s AND user_id IS NOT NULL
                    """,
                    since,
                    name,
                )
            )
        out["funnel_unique_users"] = funnel

        signups = funnel.get("signup_complete") or 0
        purchases = funnel.get("purchase_completed") or 0
        out["conversion_signup_to_purchase_pct"] = (
            round(purchases / signups * 100.0, 1) if signups else None
        )

        out["voice_users"] = int(
            _scalar(
                cur,
                """
                SELECT COUNT(DISTINCT user_id)
                FROM public.analytics_events
                WHERE event_name = 'voice_session_end'
                  AND created_at >= %s AND user_id IS NOT NULL
                """,
                since,
            )
        )
        out["spoken_seconds_total"] = float(
            _scalar(
                cur,
                """
                SELECT COALESCE(SUM((properties->>'spoken_seconds')::numeric), 0)
                FROM public.analytics_events
                WHERE event_name = 'voice_session_end' AND created_at >= %s
                """,
                since,
            )
        )
        voice_users = out["voice_users"] or 0
        out["avg_spoken_seconds_per_voice_user"] = (
            round(out["spoken_seconds_total"] / voice_users, 1) if voice_users else 0.0
        )

        out["purchase_events"] = int(
            _scalar(
                cur,
                """
                SELECT COUNT(*)
                FROM public.analytics_events
                WHERE event_name = 'purchase_completed' AND created_at >= %s
                """,
                since,
            )
        )

        out["rag_misses"] = int(
            _scalar(
                cur,
                """
                SELECT COUNT(*)
                FROM public.analytics_events
                WHERE event_name = 'rag_retrieval_miss' AND created_at >= %s
                """,
                since,
            )
        )
        rag_hits = int(
            _scalar(
                cur,
                """
                SELECT COUNT(*)
                FROM public.analytics_events
                WHERE event_name = 'rag_retrieval' AND created_at >= %s
                """,
                since,
            )
        )
        total_rag = rag_hits + out["rag_misses"]
        out["rag_miss_rate_pct"] = (
            round(out["rag_misses"] / total_rag * 100.0, 1) if total_rag else 0.0
        )

        try:
            cur.execute(
                """
                SELECT COUNT(*) FROM auth.users WHERE created_at >= %s
                """,
                (since,),
            )
            row = cur.fetchone()
            out["new_auth_users"] = int(row[0] or 0) if row else 0
        except Exception:
            out["new_auth_users"] = None

    return out


def _print_report(data: dict) -> None:
    days = data["period_days"]
    print(f"Sentient analytics report — last {days} day(s)")
    print(f"Since: {data['since']}\n")

    print("Funnel (unique users with event):")
    funnel = data.get("funnel_unique_users") or {}
    for name in FUNNEL_EVENTS:
        print(f"  {name:24} {funnel.get(name, 0):>6}")

    conv = data.get("conversion_signup_to_purchase_pct")
    if conv is not None:
        print(f"\nConversion signup → purchase: {conv}%")

    print("\nVoice engagement:")
    print(f"  Users with voice sessions: {data.get('voice_users', 0)}")
    print(f"  Total spoken seconds:      {data.get('spoken_seconds_total', 0):.0f}")
    print(
        f"  Avg spoken sec / voice user: {data.get('avg_spoken_seconds_per_voice_user', 0)}"
    )

    print("\nPurchases & RAG:")
    print(f"  purchase_completed events: {data.get('purchase_events', 0)}")
    print(f"  RAG miss rate:             {data.get('rag_miss_rate_pct', 0)}%")

    if data.get("new_auth_users") is not None:
        print(f"\nNew auth.users (Supabase):   {data['new_auth_users']}")

    print("\nRevenue: compare purchase_completed with Stripe Dashboard totals.")


def _write_csv(path: Path, data: dict) -> None:
    rows = [
        ("metric", "value"),
        ("period_days", data["period_days"]),
        ("since", data["since"]),
    ]
    for name, count in (data.get("funnel_unique_users") or {}).items():
        rows.append((f"funnel_{name}", count))
    for key in (
        "conversion_signup_to_purchase_pct",
        "voice_users",
        "spoken_seconds_total",
        "avg_spoken_seconds_per_voice_user",
        "purchase_events",
        "rag_miss_rate_pct",
        "new_auth_users",
    ):
        if key in data and data[key] is not None:
            rows.append((key, data[key]))

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)
    print(f"\nWrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly analytics ops report")
    parser.add_argument("--days", type=int, default=7, help="Lookback window (default 7)")
    parser.add_argument("--csv", type=Path, default=None, help="Optional CSV export path")
    args = parser.parse_args()

    data = run_report(args.days)
    _print_report(data)
    if args.csv:
        _write_csv(args.csv, data)


if __name__ == "__main__":
    main()
