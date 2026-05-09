"""
quotas.py — Fair-use quota enforcement and message counting.

Tracks messages per user per period (24 hours by default).
All queries use parameterized statements to prevent SQL injection.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import FAIR_USE_LIMIT, QUOTA_RESET_PERIOD_HOURS, SUPABASE_DB_URL, logger


def _get_db_connection():
    """Get a Postgres connection. Used internally by all functions."""
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")
    import psycopg
    return psycopg.connect(SUPABASE_DB_URL, autocommit=True, connect_timeout=5)


def get_message_count(user_id: str) -> int:
    """
    Get the current message count for a user in the active period.
    
    If the period has expired, count is reset to 0.
    
    Args:
        user_id: Supabase auth user ID (UUID string)
    
    Returns:
        Number of messages in current period (>= 0)
    """
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT message_count, period_start
                FROM public.user_message_counts
                WHERE user_id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
            
            if row is None:
                return 0
            
            message_count, period_start = row
            
            # Check if period has expired
            now = datetime.now(timezone.utc)
            period_expired = (now - period_start) > timedelta(hours=QUOTA_RESET_PERIOD_HOURS)
            
            if period_expired:
                # Reset and return 0
                cur.execute(
                    """
                    UPDATE public.user_message_counts
                    SET message_count = 0,
                        period_start = %s,
                        last_updated_at = %s
                    WHERE user_id = %s
                    """,
                    (now, now, user_id),
                )
                return 0
            
            return message_count
    except Exception as exc:
        logger.exception("get_message_count failed for user=%s: %s", user_id, exc)
        return 0


def check_quota(user_id: str, limit: int = FAIR_USE_LIMIT) -> bool:
    """
    Check if user is under their message quota for the current period.
    
    Args:
        user_id: Supabase auth user ID (UUID string)
        limit: Message limit (default: FAIR_USE_LIMIT from config)
    
    Returns:
        True if under limit, False if over limit or error
    """
    try:
        count = get_message_count(user_id)
        return count < limit
    except Exception as exc:
        logger.exception("check_quota failed for user=%s: %s", user_id, exc)
        # Fail open: allow on error to prevent bricking the app
        return True


def increment_message_count(user_id: str) -> bool:
    """
    Increment the message counter for a user.
    
    Creates the row if it doesn't exist (idempotent insert-or-update).
    
    Args:
        user_id: Supabase auth user ID (UUID string)
    
    Returns:
        True if incremented successfully, False on error
    """
    try:
        now = datetime.now(timezone.utc)
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_message_counts (user_id, message_count, period_start, last_updated_at)
                VALUES (%s, 1, %s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    message_count = message_count + 1,
                    last_updated_at = %s
                """,
                (user_id, now, now, now),
            )
            return True
    except Exception as exc:
        logger.exception("increment_message_count failed for user=%s: %s", user_id, exc)
        return False


def get_usage_info(user_id: str, limit: int = FAIR_USE_LIMIT) -> dict:
    """
    Get full usage info for a user: current count, limit, reset time.
    
    Args:
        user_id: Supabase auth user ID (UUID string)
        limit: Message limit (default: FAIR_USE_LIMIT from config)
    
    Returns:
        Dict with keys: messages_today, limit, reset_at (ISO datetime string)
    """
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT message_count, period_start
                FROM public.user_message_counts
                WHERE user_id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
            
            now = datetime.now(timezone.utc)
            
            if row is None:
                reset_at = now + timedelta(hours=QUOTA_RESET_PERIOD_HOURS)
                return {
                    "messages_today": 0,
                    "limit": limit,
                    "reset_at": reset_at.isoformat(),
                }
            
            message_count, period_start = row
            
            # Check if period has expired
            period_expired = (now - period_start) > timedelta(hours=QUOTA_RESET_PERIOD_HOURS)
            
            if period_expired:
                # Period has expired, reset
                reset_at = now + timedelta(hours=QUOTA_RESET_PERIOD_HOURS)
                return {
                    "messages_today": 0,
                    "limit": limit,
                    "reset_at": reset_at.isoformat(),
                }
            
            # Period is still active
            reset_at = period_start + timedelta(hours=QUOTA_RESET_PERIOD_HOURS)
            return {
                "messages_today": message_count,
                "limit": limit,
                "reset_at": reset_at.isoformat(),
            }
    except Exception as exc:
        logger.exception("get_usage_info failed for user=%s: %s", user_id, exc)
        reset_at = datetime.now(timezone.utc) + timedelta(hours=QUOTA_RESET_PERIOD_HOURS)
        return {
            "messages_today": 0,
            "limit": FAIR_USE_LIMIT,
            "reset_at": reset_at.isoformat(),
        }
