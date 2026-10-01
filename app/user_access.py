"""
user_access.py Trial and subscription access record (server truth).

Data model slice only. Chat enforcement and Flutter navigation come later.
Meditate in 5 Minutes is always free and does not need a row here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import SUPABASE_DB_URL, logger

_schema_bootstrapped = False

TRIAL_DURATION_DAYS = 14


@dataclass
class UserAccess:
    user_id: str
    trial_started_at: Optional[datetime] = None
    trial_ends_at: Optional[datetime] = None
    subscription_active: bool = False
    subscription_updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def trial_started(self) -> bool:
        return self.trial_started_at is not None

    @property
    def trial_active(self) -> bool:
        if self.trial_started_at is None or self.trial_ends_at is None:
            return False
        now = datetime.now(timezone.utc)
        ends = self.trial_ends_at
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)
        return now < ends

    @property
    def chat_allowed(self) -> bool:
        """Derived for later gates. Not enforced in this slice."""
        return self.trial_active or self.subscription_active


def _get_db_connection():
    from app.db import db_connection

    return db_connection()


def _ensure_access_schema() -> bool:
    global _schema_bootstrapped
    if _schema_bootstrapped or not SUPABASE_DB_URL:
        return _schema_bootstrapped

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.user_access (
                    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
                    trial_started_at TIMESTAMPTZ,
                    trial_ends_at TIMESTAMPTZ,
                    subscription_active BOOLEAN NOT NULL DEFAULT FALSE,
                    subscription_updated_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_access_trial_ends_at
                ON public.user_access (trial_ends_at)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_access_subscription_active
                ON public.user_access (subscription_active)
                WHERE subscription_active = TRUE
                """
            )
        _schema_bootstrapped = True
        return True
    except Exception as exc:
        logger.error("Failed to bootstrap user_access schema: %s", exc)
        return False


_ACCESS_COLUMNS = """
    user_id, trial_started_at, trial_ends_at,
    subscription_active, subscription_updated_at,
    created_at, updated_at
"""


def _row_to_access(row: tuple) -> UserAccess:
    return UserAccess(
        user_id=str(row[0]),
        trial_started_at=row[1],
        trial_ends_at=row[2],
        subscription_active=bool(row[3]),
        subscription_updated_at=row[4],
        created_at=row[5],
        updated_at=row[6],
    )


def empty_user_access(user_id: str) -> UserAccess:
    return UserAccess(user_id=user_id)


def get_user_access(user_id: str) -> UserAccess:
    """Return access row, or an empty in-memory record when none exists yet."""
    if not SUPABASE_DB_URL:
        return empty_user_access(user_id)
    if not _ensure_access_schema():
        return empty_user_access(user_id)

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ACCESS_COLUMNS} FROM public.user_access WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return empty_user_access(user_id)
            return _row_to_access(row)
    except Exception as exc:
        logger.error("get_user_access failed for %s: %s", user_id, exc)
        return empty_user_access(user_id)


def start_trial(user_id: str, *, duration_days: int = TRIAL_DURATION_DAYS) -> UserAccess:
    """
    Start a fourteen day trial if one has not started yet.

    Idempotent: if trial_started_at is already set, return the existing row.
    """
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")
    if not _ensure_access_schema():
        raise RuntimeError("user_access schema unavailable")
    if duration_days < 1:
        raise ValueError("duration_days must be at least 1")

    existing = get_user_access(user_id)
    if existing.trial_started_at is not None:
        return existing

    now = datetime.now(timezone.utc)
    ends = now + timedelta(days=duration_days)

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.user_access (
                    user_id, trial_started_at, trial_ends_at,
                    subscription_active, subscription_updated_at,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, FALSE, NULL, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET trial_started_at = COALESCE(
                        public.user_access.trial_started_at, EXCLUDED.trial_started_at
                    ),
                    trial_ends_at = COALESCE(
                        public.user_access.trial_ends_at, EXCLUDED.trial_ends_at
                    ),
                    updated_at = EXCLUDED.updated_at
                RETURNING {_ACCESS_COLUMNS}
                """,
                (user_id, now, ends, now, now),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("start_trial returned no row")
            return _row_to_access(row)
    except Exception as exc:
        logger.error("start_trial failed for %s: %s", user_id, exc)
        raise


def set_subscription_active(user_id: str, active: bool) -> UserAccess:
    """
    Set subscription_active. Used later by Apple or Google verification.

    Creates the access row if missing. Does not start a trial by itself.
    """
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")
    if not _ensure_access_schema():
        raise RuntimeError("user_access schema unavailable")

    now = datetime.now(timezone.utc)
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.user_access (
                    user_id, trial_started_at, trial_ends_at,
                    subscription_active, subscription_updated_at,
                    created_at, updated_at
                )
                VALUES (%s, NULL, NULL, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET subscription_active = EXCLUDED.subscription_active,
                    subscription_updated_at = EXCLUDED.subscription_updated_at,
                    updated_at = EXCLUDED.updated_at
                RETURNING {_ACCESS_COLUMNS}
                """,
                (user_id, bool(active), now, now, now),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("set_subscription_active returned no row")
            return _row_to_access(row)
    except Exception as exc:
        logger.error("set_subscription_active failed for %s: %s", user_id, exc)
        raise


def access_to_response_dict(access: UserAccess) -> dict:
    """Stable API payload fields for AccessResponse."""
    return {
        "user_id": access.user_id,
        "trial_started_at": access.trial_started_at,
        "trial_ends_at": access.trial_ends_at,
        "trial_started": access.trial_started,
        "trial_active": access.trial_active,
        "subscription_active": access.subscription_active,
        "subscription_updated_at": access.subscription_updated_at,
        "chat_allowed": access.chat_allowed,
        "meditate_in_5_free": True,
        "created_at": access.created_at,
        "updated_at": access.updated_at,
    }


def require_chat_access(user_id: Optional[str]) -> UserAccess:
    """
    Refuse companion and course chat when there is no active trial and no
    active subscription. Call from chat paths only. Media is not gated here.

    When user_id is missing (local auth off), allow. When the database URL is
    missing, allow so unit tests and local shells without Postgres still work;
    production already refuses a missing database URL at startup.
    """
    from fastapi import HTTPException

    if not user_id:
        return empty_user_access("")

    if not SUPABASE_DB_URL:
        return empty_user_access(user_id)

    access = get_user_access(user_id)
    if access.chat_allowed:
        return access

    if not access.trial_started:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Start a free trial to use chat.",
                "code": "trial_required",
                "chat_allowed": False,
                "trial_started": False,
                "trial_active": False,
                "subscription_active": False,
            },
        )

    raise HTTPException(
        status_code=403,
        detail={
            "message": "Your trial has ended. Subscribe to continue chatting.",
            "code": "subscription_required",
            "chat_allowed": False,
            "trial_started": True,
            "trial_active": False,
            "subscription_active": bool(access.subscription_active),
        },
    )
