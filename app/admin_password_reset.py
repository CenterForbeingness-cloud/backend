"""
admin_password_reset.py — Self-service password reset for existing admin accounts.

Requires TOTP on completion (same authenticator as login). Separate from invite setup.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from app.config import SUPABASE_DB_URL, logger

_RESET_TTL_HOURS = 24
_schema_bootstrapped = False


def _get_db_connection():
    from app.admin_auth import _get_db_connection

    return _get_db_connection()


def _hash_token(plain_token: str) -> str:
    return hashlib.sha256(plain_token.encode("utf-8")).hexdigest()


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


def _ensure_reset_schema() -> bool:
    global _schema_bootstrapped
    if _schema_bootstrapped or not SUPABASE_DB_URL:
        return _schema_bootstrapped

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE public.admin_users
                ADD COLUMN IF NOT EXISTS reset_token_hash TEXT
                """
            )
            cur.execute(
                """
                ALTER TABLE public.admin_users
                ADD COLUMN IF NOT EXISTS reset_expires_at TIMESTAMPTZ
                """
            )
        _schema_bootstrapped = True
        return True
    except Exception as exc:
        logger.error("Failed to bootstrap admin password reset schema: %s", exc)
        return False


def _load_reset_row(plain_token: str) -> Optional[dict[str, Any]]:
    if not plain_token.strip():
        return None
    if not _ensure_reset_schema():
        return None

    token_hash = _hash_token(plain_token.strip())
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, email, totp_secret, totp_enabled,
                       reset_expires_at, setup_completed_at, is_active
                FROM public.admin_users
                WHERE reset_token_hash = %s
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "admin_id": row[0],
                "email": row[1],
                "totp_secret": row[2],
                "totp_enabled": bool(row[3]),
                "reset_expires_at": row[4],
                "setup_completed_at": row[5],
                "is_active": bool(row[6]),
            }
    except Exception as exc:
        logger.exception("load password reset row failed: %s", exc)
        return None


def _token_valid(row: dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if not row["is_active"]:
        return False, "This admin account is inactive"
    if not row["setup_completed_at"]:
        return False, "Account setup is not complete. Use your invite link instead."
    if not row["totp_enabled"] or not row["totp_secret"]:
        return False, "Two-factor authentication is not configured for this account"
    expires = row["reset_expires_at"]
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires and datetime.now(timezone.utc) > expires:
        return False, "Reset link expired. Request a new one."
    return True, None


def request_password_reset(email: str) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Create reset token for an active, fully set-up admin.

    Returns (admin_id, plain_token, found). When found is False, plain_token is None
    (caller should still return generic success to avoid email enumeration).
    """
    if not SUPABASE_DB_URL:
        return None, None, False
    if not _ensure_reset_schema():
        return None, None, False

    normalized = email.strip().lower()
    if not normalized:
        return None, None, False

    plain_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain_token)
    expires = datetime.now(timezone.utc) + timedelta(hours=_RESET_TTL_HOURS)

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.admin_users
                SET reset_token_hash = %s,
                    reset_expires_at = %s
                WHERE lower(email) = %s
                  AND is_active = TRUE
                  AND setup_completed_at IS NOT NULL
                  AND totp_enabled = TRUE
                  AND totp_secret IS NOT NULL
                RETURNING id::text
                """,
                (token_hash, expires, normalized),
            )
            row = cur.fetchone()
            if not row:
                return None, None, False
            return str(row[0]), plain_token, True
    except Exception as exc:
        logger.exception("request_password_reset failed: %s", exc)
        return None, None, False


def request_password_reset_by_admin_id(admin_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Owner-initiated reset. Returns (email, plain_token, error).
    """
    if not SUPABASE_DB_URL:
        return None, None, "Database not configured"
    if not _ensure_reset_schema():
        return None, None, "Password reset schema unavailable"

    plain_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain_token)
    expires = datetime.now(timezone.utc) + timedelta(hours=_RESET_TTL_HOURS)

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.admin_users
                SET reset_token_hash = %s,
                    reset_expires_at = %s
                WHERE id = %s::uuid
                  AND is_active = TRUE
                  AND setup_completed_at IS NOT NULL
                  AND totp_enabled = TRUE
                  AND totp_secret IS NOT NULL
                RETURNING email
                """,
                (token_hash, expires, admin_id),
            )
            row = cur.fetchone()
            if not row:
                return None, None, "Admin not found or account not ready for reset"
            return str(row[0]), plain_token, None
    except Exception as exc:
        logger.exception("request_password_reset_by_admin_id failed: %s", exc)
        return None, None, "Failed to create password reset"


def reset_status(plain_token: str) -> Tuple[Optional[dict], Optional[str]]:
    row = _load_reset_row(plain_token)
    if not row:
        return None, "Invalid or expired reset link"
    ok, err = _token_valid(row)
    if not ok:
        return None, err
    return {
        "email_hint": _mask_email(str(row["email"])),
        "role": None,
    }, None


def complete_password_reset(
    plain_token: str,
    password: str,
    totp_code: str,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Return (ok, error, admin_id_for_audit)."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters", None

    row = _load_reset_row(plain_token)
    if not row:
        return False, "Invalid or expired reset link", None
    ok, err = _token_valid(row)
    if not ok:
        return False, err, None

    import bcrypt
    import pyotp

    totp = pyotp.TOTP(row["totp_secret"])
    if not totp.verify(totp_code.strip(), valid_window=1):
        return False, "Invalid authenticator code. Try the next code from your app.", None

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    now = datetime.now(timezone.utc)
    token_hash = _hash_token(plain_token.strip())

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.admin_users
                SET password_hash = %s,
                    reset_token_hash = NULL,
                    reset_expires_at = NULL,
                    password_changed_at = %s
                WHERE reset_token_hash = %s
                """,
                (password_hash, now, token_hash),
            )
            if cur.rowcount != 1:
                return False, "Password could not be reset", None
        logger.info("Admin password reset completed: %s", row["email"])
        return True, None, row["admin_id"]
    except Exception as exc:
        logger.exception("complete_password_reset failed: %s", exc)
        return False, "Failed to reset password", None
