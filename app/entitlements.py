"""
entitlements.py — Course ownership and entitlement management.

Queries user_entitlements table to check course access and grant/revoke permissions.
All queries use parameterized statements to prevent SQL injection.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import SUPABASE_DB_URL, logger

# Product SKUs (Stripe) that are not daily-schedule courses.
PRODUCT_ONLY_SLUGS: frozenset[str] = frozenset({"starter-bundle"})

# Courses unlocked when a bundle SKU is purchased (also lazy-synced on GET /entitlements).
BUNDLE_INCLUDED_COURSES: dict[str, tuple[str, ...]] = {
    "starter-bundle": (
        "mindful-foundations",
        "week-zero-reset",
        "deep-calm-protocol",
        "focus-discipline",
    ),
}

_schema_bootstrapped = False


def is_product_only_slug(course_slug: str) -> bool:
    return course_slug in PRODUCT_ONLY_SLUGS


def entitlement_grants_for_product(course_slug: str) -> list[str]:
    """Slugs to grant for a purchase (bundle expands to included courses)."""
    grants = [course_slug]
    grants.extend(BUNDLE_INCLUDED_COURSES.get(course_slug, ()))
    seen: set[str] = set()
    ordered: list[str] = []
    for slug in grants:
        if slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def sync_bundle_child_entitlements(user_id: str, owned: list[str]) -> list[str]:
    """Ensure bundle purchases also have rows for included courses (idempotent)."""
    owned_set = set(owned)
    changed = False
    for slug in list(owned_set):
        for child in BUNDLE_INCLUDED_COURSES.get(slug, ()):
            if child in owned_set:
                continue
            if grant_entitlement(user_id=user_id, course_slug=child, granted_by="stripe"):
                owned_set.add(child)
                changed = True
    if changed:
        return sorted(owned_set)
    return owned


def _get_db_connection():
    """Get a Postgres connection from the shared pool."""
    from app.db import db_connection

    return db_connection()


def _ensure_entitlements_schema() -> None:
    """Create or patch entitlement tables required by this module (idempotent)."""
    global _schema_bootstrapped
    if _schema_bootstrapped:
        return

    with _get_db_connection() as conn, conn.cursor() as cur:
        # Base tables
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.user_entitlements (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                course_slug TEXT NOT NULL,
                granted_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
                granted_by TEXT NOT NULL DEFAULT 'admin',
                expires_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ,
                revoked_by TEXT,
                revoke_reason TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.course_purchases (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
                course_slug TEXT NOT NULL,
                purchase_source TEXT NOT NULL DEFAULT 'stripe',
                stripe_session_id TEXT,
                stripe_payment_intent_id TEXT,
                purchased_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
                refunded_at TIMESTAMPTZ
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.purchase_events (
                id BIGSERIAL PRIMARY KEY,
                stripe_event_id TEXT,
                stripe_event_type TEXT,
                stripe_session_id TEXT,
                user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
                course_slug TEXT,
                received_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
                processed_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
                processing_status TEXT NOT NULL DEFAULT 'success',
                processing_error TEXT,
                idempotency_key TEXT
            )
            """
        )

        # Compatibility columns for pre-existing schemas
        cur.execute("ALTER TABLE public.user_entitlements ADD COLUMN IF NOT EXISTS granted_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())")
        cur.execute("ALTER TABLE public.user_entitlements ADD COLUMN IF NOT EXISTS granted_by TEXT")
        cur.execute("ALTER TABLE public.user_entitlements ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
        cur.execute("ALTER TABLE public.user_entitlements ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ")
        cur.execute("ALTER TABLE public.user_entitlements ADD COLUMN IF NOT EXISTS revoked_by TEXT")
        cur.execute("ALTER TABLE public.user_entitlements ADD COLUMN IF NOT EXISTS revoke_reason TEXT")

        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS stripe_event_id TEXT")
        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS stripe_event_type TEXT")
        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS stripe_session_id TEXT")
        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS user_id UUID")
        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS course_slug TEXT")
        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())")
        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS processing_status TEXT NOT NULL DEFAULT 'success'")
        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS processing_error TEXT")
        cur.execute("ALTER TABLE public.purchase_events ADD COLUMN IF NOT EXISTS idempotency_key TEXT")

        cur.execute(
            "ALTER TABLE public.course_purchases ADD COLUMN IF NOT EXISTS purchase_source TEXT NOT NULL DEFAULT 'stripe'"
        )
        cur.execute(
            "ALTER TABLE public.course_purchases ADD COLUMN IF NOT EXISTS stripe_session_id TEXT"
        )
        cur.execute(
            "ALTER TABLE public.course_purchases ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT"
        )
        cur.execute(
            "ALTER TABLE public.course_purchases ADD COLUMN IF NOT EXISTS purchased_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())"
        )
        cur.execute(
            "ALTER TABLE public.course_purchases ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ"
        )

        # Helpful indexes
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_course_purchases_stripe_session_unique
            ON public.course_purchases(stripe_session_id)
            WHERE stripe_session_id IS NOT NULL AND refunded_at IS NULL
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_course_purchases_stripe_pi_unique
            ON public.course_purchases(stripe_payment_intent_id)
            WHERE stripe_payment_intent_id IS NOT NULL AND refunded_at IS NULL
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_user_entitlements_active_unique
            ON public.user_entitlements(user_id, course_slug)
            WHERE revoked_at IS NULL
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_events_stripe_event_id_unique
            ON public.purchase_events(stripe_event_id)
            WHERE stripe_event_id IS NOT NULL
            """
        )

    _schema_bootstrapped = True


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
    _ensure_entitlements_schema()
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
            if row is not None:
                return True

            for bundle_slug, children in BUNDLE_INCLUDED_COURSES.items():
                if course_slug not in children:
                    continue
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
                    (user_id, bundle_slug),
                )
                if cur.fetchone() is not None:
                    return True
            return False
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
    _ensure_entitlements_schema()
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
            owned = [row[0] for row in rows]
            return sync_bundle_child_entitlements(user_id, owned)
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
    _ensure_entitlements_schema()
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
    _ensure_entitlements_schema()
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


def record_course_purchase(
    user_id: str,
    course_slug: str,
    *,
    purchase_source: str = "stripe",
    stripe_session_id: Optional[str] = None,
    stripe_payment_intent_id: Optional[str] = None,
) -> bool:
    """
    Persist an immutable purchase row (idempotent on Stripe session or payment intent).
    """
    _ensure_entitlements_schema()
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            if stripe_session_id:
                cur.execute(
                    """
                    SELECT id FROM public.course_purchases
                    WHERE stripe_session_id = %s AND refunded_at IS NULL
                    LIMIT 1
                    """,
                    (stripe_session_id,),
                )
                if cur.fetchone() is not None:
                    return True

            if stripe_payment_intent_id:
                cur.execute(
                    """
                    SELECT id FROM public.course_purchases
                    WHERE stripe_payment_intent_id = %s AND refunded_at IS NULL
                    LIMIT 1
                    """,
                    (stripe_payment_intent_id,),
                )
                if cur.fetchone() is not None:
                    return True

            cur.execute(
                """
                INSERT INTO public.course_purchases (
                    user_id, course_slug, purchase_source,
                    stripe_session_id, stripe_payment_intent_id
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    course_slug,
                    purchase_source,
                    stripe_session_id,
                    stripe_payment_intent_id,
                ),
            )
            logger.info(
                "Recorded course purchase user=%s course=%s session=%s intent=%s",
                user_id,
                course_slug,
                stripe_session_id,
                stripe_payment_intent_id,
            )
            return True
    except Exception as exc:
        if "unique constraint" in str(exc).lower() or "duplicate" in str(exc).lower():
            return True
        logger.exception(
            "record_course_purchase failed user=%s course=%s: %s",
            user_id,
            course_slug,
            exc,
        )
        return False


def mark_course_purchase_refunded(
    user_id: str,
    course_slug: str,
    *,
    stripe_session_id: Optional[str] = None,
    stripe_payment_intent_id: Optional[str] = None,
) -> bool:
    """Mark matching purchase rows refunded (best-effort when Stripe metadata is sparse)."""
    _ensure_entitlements_schema()
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            if stripe_session_id:
                cur.execute(
                    """
                    UPDATE public.course_purchases
                    SET refunded_at = timezone('utc'::text, now())
                    WHERE user_id = %s
                      AND course_slug = %s
                      AND stripe_session_id = %s
                      AND refunded_at IS NULL
                    """,
                    (user_id, course_slug, stripe_session_id),
                )
            elif stripe_payment_intent_id:
                cur.execute(
                    """
                    UPDATE public.course_purchases
                    SET refunded_at = timezone('utc'::text, now())
                    WHERE user_id = %s
                      AND course_slug = %s
                      AND stripe_payment_intent_id = %s
                      AND refunded_at IS NULL
                    """,
                    (user_id, course_slug, stripe_payment_intent_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE public.course_purchases
                    SET refunded_at = timezone('utc'::text, now())
                    WHERE user_id = %s
                      AND course_slug = %s
                      AND refunded_at IS NULL
                    """,
                    (user_id, course_slug),
                )
            return True
    except Exception as exc:
        logger.exception(
            "mark_course_purchase_refunded failed user=%s course=%s: %s",
            user_id,
            course_slug,
            exc,
        )
        return False


def record_purchase_event(
    stripe_event_id: str,
    stripe_event_type: str,
    stripe_session_id: str,
    user_id: str,
    course_slug: str,
) -> str:
    """
    Record a purchase event for audit/idempotency.

    Returns:
        "new" — first time this Stripe event id was seen
        "duplicate" — event already recorded (skip grant side effects)
        "error" — database failure
    """
    _ensure_entitlements_schema()
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
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
                logger.info(
                    "Recorded purchase event event_id=%s user=%s course=%s",
                    stripe_event_id,
                    user_id,
                    course_slug,
                )
                return "new"
            except Exception as e:
                if "unique constraint" in str(e).lower() or "duplicate" in str(e).lower():
                    logger.info(
                        "Purchase event already recorded (idempotent) event_id=%s",
                        stripe_event_id,
                    )
                    return "duplicate"
                raise
    except Exception as exc:
        logger.exception("record_purchase_event failed event_id=%s: %s", stripe_event_id, exc)
        return "error"


def apply_purchase_grant(
    user_id: str,
    course_slug: str,
    *,
    stripe_event_id: str,
    stripe_event_type: str,
    stripe_reference_id: str,
    stripe_session_id: Optional[str] = None,
    stripe_payment_intent_id: Optional[str] = None,
) -> bool:
    """
    Idempotent grant path: record webhook event once, then purchase row + entitlement.
    """
    event_status = record_purchase_event(
        stripe_event_id=stripe_event_id,
        stripe_event_type=stripe_event_type,
        stripe_session_id=stripe_reference_id,
        user_id=user_id,
        course_slug=course_slug,
    )
    if event_status == "duplicate":
        return True
    if event_status == "error":
        return False

    if not record_course_purchase(
        user_id,
        course_slug,
        stripe_session_id=stripe_session_id,
        stripe_payment_intent_id=stripe_payment_intent_id,
    ):
        return False

    ok = True
    for slug in entitlement_grants_for_product(course_slug):
        if not grant_entitlement(user_id=user_id, course_slug=slug, granted_by="stripe"):
            ok = False
    return ok


def apply_purchase_revoke(
    user_id: str,
    course_slug: str,
    *,
    stripe_event_id: str,
    stripe_event_type: str,
    stripe_reference_id: str,
    reason: str,
    stripe_session_id: Optional[str] = None,
    stripe_payment_intent_id: Optional[str] = None,
) -> bool:
    """Idempotent revoke path for refunds and failed checkouts."""
    event_status = record_purchase_event(
        stripe_event_id=stripe_event_id,
        stripe_event_type=stripe_event_type,
        stripe_session_id=stripe_reference_id,
        user_id=user_id,
        course_slug=course_slug,
    )
    if event_status == "error":
        return False
    if event_status == "duplicate":
        return True

    mark_course_purchase_refunded(
        user_id,
        course_slug,
        stripe_session_id=stripe_session_id,
        stripe_payment_intent_id=stripe_payment_intent_id,
    )
    ok = True
    for slug in entitlement_grants_for_product(course_slug):
        if not revoke_entitlement(user_id=user_id, course_slug=slug, reason=reason):
            ok = False
    return ok
