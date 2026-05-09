"""
entitlements.py — Course ownership and entitlement management.

Queries user_entitlements table to check course access and grant/revoke permissions.
All queries use parameterized statements to prevent SQL injection.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import SUPABASE_DB_URL, logger


def _get_db_connection():
    """Get a Postgres connection. Used internally by all functions."""
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")
    import psycopg
    return psycopg.connect(SUPABASE_DB_URL, autocommit=True, connect_timeout=5)


def check_entitlement(user_id: str, course_slug: str) -> bool:
    """
    Check if user owns (has an active, non-revoked entitlement for) a course.
    
    Returns True if:
    - An entitlement row exists for (user_id, course_slug)
    - revoked_at IS NULL
    - expires_at IS NULL OR expires_at > now
    
    Returns False otherwise.
    
    Args:
        user_id: Supabase auth user ID (UUID string)
        course_slug: Course identifier (alphanumeric + hyphens)
    
    Returns:
        True if user owns course, False otherwise
    
    Raises:
        RuntimeError: If database is not configured
    """
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM public.user_entitlements
                WHERE user_id = %s
                  AND course_slug = %s
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > timezone('utc'::text, now()))
                LIMIT 1
                """,
                (user_id, course_slug),
            )
            row = cur.fetchone()
            return row is not None
    except Exception as exc:
        logger.exception("check_entitlement failed for user=%s course=%s: %s", user_id, course_slug, exc)
        return False


def get_user_entitlements(user_id: str) -> list[str]:
    """
    Return list of course slugs the user owns (active entitlements).
    
    Args:
        user_id: Supabase auth user ID (UUID string)
    
    Returns:
        List of course_slug strings
    """
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT course_slug
                FROM public.user_entitlements
                WHERE user_id = %s
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > timezone('utc'::text, now()))
                ORDER BY course_slug
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            return [row[0] for row in rows]
    except Exception as exc:
        logger.exception("get_user_entitlements failed for user=%s: %s", user_id, exc)
        return []


def grant_entitlement(
    user_id: str,
    course_slug: str,
    granted_by: str,
    expires_at: Optional[datetime] = None,
) -> bool:
    """
    Grant a course entitlement to a user.
    
    If an active entitlement already exists, this is a no-op (idempotent).
    If a revoked entitlement exists, create a new one.
    
    Args:
        user_id: Supabase auth user ID (UUID string)
        course_slug: Course identifier
        granted_by: One of 'stripe', 'apple', 'google', 'admin'
        expires_at: Optional expiry datetime (UTC). None means no expiry.
    
    Returns:
        True if granted, False on error
    """
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            # Check if active entitlement already exists
            cur.execute(
                """
                SELECT id
                FROM public.user_entitlements
                WHERE user_id = %s
                  AND course_slug = %s
                  AND revoked_at IS NULL
                LIMIT 1
                """,
                (user_id, course_slug),
            )
            if cur.fetchone() is not None:
                logger.info("Entitlement already exists for user=%s course=%s, skipping", user_id, course_slug)
                return True
            
            # Insert new entitlement
            cur.execute(
                """
                INSERT INTO public.user_entitlements (user_id, course_slug, granted_by, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, course_slug, granted_by, expires_at),
            )
            logger.info("Granted entitlement to user=%s course=%s via %s", user_id, course_slug, granted_by)
            return True
    except Exception as exc:
        logger.exception("grant_entitlement failed for user=%s course=%s: %s", user_id, course_slug, exc)
        return False


def revoke_entitlement(user_id: str, course_slug: str, reason: str = "admin_revoke") -> bool:
    """
    Revoke a user's course entitlement.
    
    Args:
        user_id: Supabase auth user ID (UUID string)
        course_slug: Course identifier
        reason: Revocation reason (e.g. 'refund', 'expiry', 'admin_revoke')
    
    Returns:
        True if revoked, False on error
    """
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.user_entitlements
                SET revoked_at = timezone('utc'::text, now()),
                    revoked_by = %s,
                    revoke_reason = %s
                WHERE user_id = %s
                  AND course_slug = %s
                  AND revoked_at IS NULL
                """,
                ('admin', reason, user_id, course_slug),
            )
            logger.info("Revoked entitlement for user=%s course=%s reason=%s", user_id, course_slug, reason)
            return True
    except Exception as exc:
        logger.exception("revoke_entitlement failed for user=%s course=%s: %s", user_id, course_slug, exc)
        return False


def record_purchase_event(
    stripe_event_id: str,
    stripe_event_type: str,
    stripe_session_id: str,
    user_id: str,
    course_slug: str,
) -> bool:
    """
    Record a purchase event for audit/idempotency.
    
    If stripe_event_id already exists, returns True (idempotent).
    
    Args:
        stripe_event_id: Stripe event ID (e.g. 'evt_...')
        stripe_event_type: Event type (e.g. 'checkout.session.completed')
        stripe_session_id: Stripe session ID
        user_id: Supabase user ID
        course_slug: Course being purchased
    
    Returns:
        True if recorded or already exists, False on error
    """
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            # Try insert with unique stripe_event_id constraint
            # If already exists, it will raise an integrity error which we catch and ignore
            try:
                cur.execute(
                    """
                    INSERT INTO public.purchase_events (
                        stripe_event_id, stripe_event_type, stripe_session_id, user_id, course_slug
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (stripe_event_id, stripe_event_type, stripe_session_id, user_id, course_slug),
                )
                logger.info("Recorded purchase event event_id=%s user=%s course=%s", stripe_event_id, user_id, course_slug)
                return True
            except Exception as e:
                # Check if it's a unique constraint violation (event already recorded)
                if "unique constraint" in str(e).lower() or "duplicate" in str(e).lower():
                    logger.info("Purchase event already recorded (idempotent) event_id=%s", stripe_event_id)
                    return True
                raise
    except Exception as exc:
        logger.exception("record_purchase_event failed event_id=%s: %s", stripe_event_id, exc)
        return False
