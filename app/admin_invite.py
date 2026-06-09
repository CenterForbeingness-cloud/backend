"""
admin_invite.py — Email invite + self-service password and TOTP enrollment.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from app.config import ADMIN_2FA_ISSUER, SUPABASE_DB_URL, logger

_INVITE_TTL_HOURS = 72
_schema_bootstrapped = False


def _get_db_connection():
    from app.admin_auth import _get_db_connection

    return _get_db_connection()


def _hash_token(plain_token: str) -> str:
    return hashlib.sha256(plain_token.encode("utf-8")).hexdigest()


def _ensure_invite_schema() -> bool:
    global _schema_bootstrapped
    if _schema_bootstrapped or not SUPABASE_DB_URL:
        return _schema_bootstrapped

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE public.admin_users
                ADD COLUMN IF NOT EXISTS invite_token_hash TEXT
                """
            )
            cur.execute(
                """
                ALTER TABLE public.admin_users
                ADD COLUMN IF NOT EXISTS invite_expires_at TIMESTAMPTZ
                """
            )
            cur.execute(
                """
                ALTER TABLE public.admin_users
                ADD COLUMN IF NOT EXISTS setup_completed_at TIMESTAMPTZ
                """
            )
        _schema_bootstrapped = True
        return True
    except Exception as exc:
        logger.error("Failed to bootstrap admin invite schema: %s", exc)
        return False


def create_admin_invite(email: str, role: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Create pending admin and return (admin_id, plain_invite_token, error).
    """
    allowed = {"owner", "editor", "viewer"}
    if role not in allowed:
        return None, None, f"Invalid role. Must be one of: {', '.join(sorted(allowed))}"

    if not SUPABASE_DB_URL:
        return None, None, "Database not configured"
    if not _ensure_invite_schema():
        return None, None, "Invite schema unavailable"

    import bcrypt

    placeholder = bcrypt.hashpw(
        secrets.token_urlsafe(48).encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    plain_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain_token)
    expires = datetime.now(timezone.utc) + timedelta(hours=_INVITE_TTL_HOURS)

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.admin_users (
                    email, password_hash, role, is_active,
                    totp_enabled, totp_secret,
                    invite_token_hash, invite_expires_at
                )
                VALUES (%s, %s, %s, TRUE, FALSE, NULL, %s, %s)
                RETURNING id::text
                """,
                (email.strip().lower(), placeholder, role, token_hash, expires),
            )
            row = cur.fetchone()
            if not row:
                return None, None, "Failed to create invite"
            return str(row[0]), plain_token, None
    except Exception as exc:
        if "unique constraint" in str(exc).lower():
            return None, None, "Email already registered as admin"
        logger.exception("create_admin_invite failed: %s", exc)
        return None, None, "Failed to create invite"


def _load_invite_row(plain_token: str) -> Optional[dict[str, Any]]:
    if not plain_token.strip():
        return None
    if not _ensure_invite_schema():
        return None

    token_hash = _hash_token(plain_token.strip())
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, email, role, totp_secret, totp_enabled,
                       invite_expires_at, setup_completed_at, is_active
                FROM public.admin_users
                WHERE invite_token_hash = %s
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "admin_id": row[0],
                "email": row[1],
                "role": row[2],
                "totp_secret": row[3],
                "totp_enabled": bool(row[4]),
                "invite_expires_at": row[5],
                "setup_completed_at": row[6],
                "is_active": bool(row[7]),
            }
    except Exception as exc:
        logger.exception("load invite failed: %s", exc)
        return None


def invite_status(plain_token: str) -> Tuple[Optional[dict], Optional[str]]:
    row = _load_invite_row(plain_token)
    if not row:
        return None, "Invalid or expired invite link"
    if not row["is_active"]:
        return None, "This admin account is inactive"
    if row["totp_enabled"] and row["setup_completed_at"]:
        return None, "This invite was already used. Sign in instead."

    expires = row["invite_expires_at"]
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires and datetime.now(timezone.utc) > expires:
        return None, "Invite link expired. Ask an owner to send a new invite."

    return {
        "email": row["email"],
        "role": row["role"],
        "totp_configured": bool(row["totp_secret"]),
    }, None


def begin_invite_setup(plain_token: str) -> Tuple[Optional[dict], Optional[str]]:
    """Generate (or return) TOTP secret for QR scan; not enabled until complete."""
    row = _load_invite_row(plain_token)
    if not row:
        return None, "Invalid or expired invite link"
    if row["totp_enabled"]:
        return None, "Account already set up. Sign in."

    expires = row["invite_expires_at"]
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires and datetime.now(timezone.utc) > expires:
        return None, "Invite link expired"

    import pyotp

    from app.admin_auth import generate_totp_secret

    secret = row["totp_secret"] or generate_totp_secret()

    if not row["totp_secret"]:
        token_hash = _hash_token(plain_token.strip())
        try:
            with _get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.admin_users
                    SET totp_secret = %s, totp_enabled = FALSE
                    WHERE invite_token_hash = %s
                    """,
                    (secret, token_hash),
                )
        except Exception as exc:
            logger.exception("begin_invite_setup save secret failed: %s", exc)
            return None, "Failed to prepare authenticator setup"

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=row["email"], issuer_name=ADMIN_2FA_ISSUER)
    return {
        "email": row["email"],
        "role": row["role"],
        "totp_provisioning_uri": uri,
        "issuer": ADMIN_2FA_ISSUER,
    }, None


def complete_invite_setup(
    plain_token: str,
    password: str,
    totp_code: str,
) -> Tuple[bool, Optional[str]]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters"

    row = _load_invite_row(plain_token)
    if not row:
        return False, "Invalid or expired invite link"
    if not row["totp_secret"]:
        return False, "Scan the QR code first (refresh setup page)"

    expires = row["invite_expires_at"]
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires and datetime.now(timezone.utc) > expires:
        return False, "Invite link expired"

    import bcrypt
    import pyotp

    totp = pyotp.TOTP(row["totp_secret"])
    if not totp.verify(totp_code.strip(), valid_window=1):
        return False, "Invalid authenticator code. Try the next code from your app."

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    now = datetime.now(timezone.utc)
    token_hash = _hash_token(plain_token.strip())

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.admin_users
                SET password_hash = %s,
                    totp_enabled = TRUE,
                    invite_token_hash = NULL,
                    invite_expires_at = NULL,
                    setup_completed_at = %s,
                    password_changed_at = %s
                WHERE invite_token_hash = %s
                """,
                (password_hash, now, now, token_hash),
            )
            if cur.rowcount != 1:
                return False, "Invite could not be completed"
        logger.info("Admin invite setup completed: %s", row["email"])
        return True, None
    except Exception as exc:
        logger.exception("complete_invite_setup failed: %s", exc)
        return False, "Failed to complete setup"
