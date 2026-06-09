"""
admin_auth.py — Admin authentication with 2FA (TOTP).

Handles admin login, TOTP verification, and JWT token generation.
All passwords are bcrypt-hashed; TOTP uses RFC 6238 (standard authenticator apps).
"""

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app.config import SUPABASE_JWT_SECRET, logger


def _get_db_connection():
    """Get a Postgres connection. Used internally by all functions."""
    from app.config import SUPABASE_DB_URL
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")
    import psycopg
    return psycopg.connect(SUPABASE_DB_URL, autocommit=True, connect_timeout=5)


def admin_login(email: str, password: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Validate admin credentials and return a session token for 2FA verification.
    
    Args:
        email: Admin email address
        password: Admin password (plaintext, will be bcrypt-verified)
    
    Returns:
        Tuple[session_token, error_message]
        If success: (session_token, None)
        If failure: (None, error_message)
    """
    try:
        import bcrypt
        
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, password_hash, totp_secret, totp_enabled
                FROM public.admin_users
                WHERE email = %s AND is_active = TRUE
                """,
                (email,),
            )
            row = cur.fetchone()
            
            if row is None:
                logger.warning("Admin login failed: email not found or inactive: %s", email)
                return None, "Invalid credentials"
            
            admin_id, password_hash, totp_secret, totp_enabled = row

            if not totp_enabled:
                logger.warning("Admin login blocked: setup incomplete for %s", email)
                return None, (
                    "Account setup is incomplete. Open the invite link from your email "
                    "to set your password and authenticator app."
                )

            # Verify password with bcrypt
            try:
                if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
                    logger.warning("Admin login failed: password mismatch for %s", email)
                    return None, "Invalid credentials"
            except Exception as exc:
                logger.exception("bcrypt verification failed: %s", exc)
                return None, "Authentication error"
            
            # Generate session token for 2FA challenge
            session_token = secrets.token_urlsafe(32)
            
            # Store session token in a temporary location (in-memory or short-lived cache)
            # For now, we return it and the client must send it back with TOTP code
            # In production, you'd store this in Redis with 10-minute TTL
            logger.info("Admin login successful (awaiting 2FA): %s", email)
            return session_token, None
    except Exception as exc:
        logger.exception("admin_login failed: %s", exc)
        return None, "Authentication error"


def verify_totp(email: str, totp_code: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Verify TOTP code and return admin JWT token if successful.
    
    Args:
        email: Admin email address
        totp_code: 6-digit TOTP code from authenticator app
    
    Returns:
        Tuple[admin_token, error_message]
        If success: (admin_token, None)
        If failure: (None, error_message)
    """
    try:
        import pyotp
        
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, totp_secret, role
                FROM public.admin_users
                WHERE email = %s AND is_active = TRUE AND totp_enabled = TRUE
                """,
                (email,),
            )
            row = cur.fetchone()
            
            if row is None:
                logger.warning("TOTP verification failed: admin not found or 2FA not enabled: %s", email)
                return None, "Admin not found"
            
            admin_id, totp_secret, role = row
            
            if not totp_secret:
                logger.warning("TOTP verification failed: no secret configured for %s", email)
                return None, "2FA not configured"
            
            # Verify TOTP code (RFC 6238)
            # Allow ±1 time window for clock skew (default pyotp window is 1)
            totp = pyotp.TOTP(totp_secret)
            if not totp.verify(totp_code, valid_window=1):
                logger.warning("TOTP verification failed: invalid code for %s", email)
                return None, "Invalid authenticator code"
            
            # Generate admin JWT token
            admin_token = _generate_admin_jwt(admin_id, email, role)
            
            # Update last_login timestamp
            now = datetime.now(timezone.utc)
            try:
                cur.execute(
                    """
                    UPDATE public.admin_users
                    SET last_login = %s
                    WHERE id = %s
                    """,
                    (now, admin_id),
                )
            except Exception as exc:
                logger.warning("Failed to update last_login: %s", exc)
            
            logger.info("Admin login successful with 2FA: %s role=%s", email, role)
            return admin_token, None
    except Exception as exc:
        logger.exception("verify_totp failed: %s", exc)
        return None, "Verification error"


def _generate_admin_jwt(admin_id: str, email: str, role: str) -> str:
    """
    Generate a JWT token for authenticated admin.
    
    Args:
        admin_id: Admin user UUID
        email: Admin email
        role: Admin role ('owner', 'editor', 'viewer')
    
    Returns:
        JWT token string
    """
    import jwt
    
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=8)  # 8-hour session
    
    payload = {
        "sub": str(admin_id),
        "email": email,
        "admin_role": role,
        "type": "admin",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    
    if not SUPABASE_JWT_SECRET:
        raise ValueError("SUPABASE_JWT_SECRET not configured")
    
    token = jwt.encode(payload, SUPABASE_JWT_SECRET, algorithm="HS256")
    return token


def verify_admin_token(token: str) -> Optional[dict]:
    """
    Verify an admin JWT token and return the payload.
    
    Args:
        token: JWT token string
    
    Returns:
        Payload dict if valid, None if invalid/expired
    """
    import jwt
    
    try:
        if not SUPABASE_JWT_SECRET:
            return None
        
        payload = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"])
        
        # Verify it's an admin token
        if payload.get("type") != "admin":
            logger.warning("Token verification failed: not an admin token")
            return None
        
        return payload
    except jwt.ExpiredSignatureError:
        logger.info("Admin token expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("Admin token verification failed: %s", exc)
        return None


def create_admin_user(email: str, password: str, role: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Create a new admin user with bcrypt-hashed password.
    TOTP secret must be generated separately via generate_totp_secret().

    Returns:
        Tuple[admin_id, error_message] — admin_id set on success.
    """
    allowed = {"owner", "editor", "viewer"}
    if role not in allowed:
        return None, f"Invalid role. Must be one of: {', '.join(sorted(allowed))}"

    try:
        import bcrypt

        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")

        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.admin_users (email, password_hash, role, is_active)
                VALUES (%s, %s, %s, TRUE)
                RETURNING id::text
                """,
                (email.strip(), password_hash, role),
            )
            row = cur.fetchone()
            if not row:
                return None, "Failed to create admin user"
            admin_id = str(row[0])
            logger.info("Created admin user: %s role=%s id=%s", email, role, admin_id)
            return admin_id, None
    except Exception as exc:
        if "unique constraint" in str(exc).lower():
            logger.warning("Admin user already exists: %s", email)
            return None, "Email already registered as admin"
        logger.exception("create_admin_user failed: %s", exc)
        return None, "Failed to create admin user"


def list_admin_staff() -> list[dict]:
    """Return all admin_users rows (for owner/editor staff management UI)."""
    with _get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text,
                email,
                role,
                is_active,
                totp_enabled,
                last_login
            FROM public.admin_users
            ORDER BY email
            """
        )
        rows = cur.fetchall()
    return [
        {
            "admin_id": row[0],
            "email": row[1],
            "role": row[2],
            "is_active": bool(row[3]),
            "totp_enabled": bool(row[4]),
            "last_login": row[5],
        }
        for row in rows
    ]


def update_admin_role(admin_id: str, new_role: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Change an admin_users.role value.

    Returns (updated_row_dict, error_message).
    """
    allowed = {"owner", "editor", "viewer"}
    if new_role not in allowed:
        return None, f"Invalid role. Must be one of: {', '.join(sorted(allowed))}"

    try:
        admin_uuid = str(__import__("uuid").UUID(admin_id))
    except ValueError:
        return None, "Invalid admin_id"

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT email, role
                FROM public.admin_users
                WHERE id = %s
                """,
                (admin_uuid,),
            )
            row = cur.fetchone()
            if row is None:
                return None, "Admin user not found"

            old_email, old_role = row[0], row[1]
            if old_role == new_role:
                return {
                    "admin_id": admin_uuid,
                    "email": old_email,
                    "role": new_role,
                    "previous_role": old_role,
                }, None

            cur.execute(
                """
                UPDATE public.admin_users
                SET role = %s
                WHERE id = %s
                RETURNING id::text, email, role, is_active, totp_enabled, last_login
                """,
                (new_role, admin_uuid),
            )
            updated = cur.fetchone()
            if not updated:
                return None, "Failed to update role"

            return {
                "admin_id": updated[0],
                "email": updated[1],
                "role": updated[2],
                "is_active": bool(updated[3]),
                "totp_enabled": bool(updated[4]),
                "last_login": updated[5],
                "previous_role": old_role,
            }, None
    except Exception as exc:
        logger.exception("update_admin_role failed admin=%s: %s", admin_id, exc)
        return None, "Failed to update admin role"


def update_admin_staff(
    admin_id: str,
    *,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Update role and/or is_active for an admin_users row.

    Returns (updated_row_dict, error_message).
    """
    if role is None and is_active is None:
        return None, "Provide role and/or is_active"

    try:
        admin_uuid = str(__import__("uuid").UUID(admin_id))
    except ValueError:
        return None, "Invalid admin_id"

    updated_row: Optional[dict] = None
    error: Optional[str] = None

    if role is not None:
        updated_row, error = update_admin_role(admin_uuid, role)
        if error:
            return None, error

    if is_active is not None:
        try:
            with _get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT email, role, is_active
                    FROM public.admin_users
                    WHERE id = %s
                    """,
                    (admin_uuid,),
                )
                row = cur.fetchone()
                if row is None:
                    return None, "Admin user not found"

                old_email, old_role, old_active = row[0], row[1], bool(row[2])
                if old_active == is_active:
                    active_row = updated_row or {
                        "admin_id": admin_uuid,
                        "email": old_email,
                        "role": old_role,
                        "is_active": is_active,
                        "previous_is_active": old_active,
                    }
                    updated_row = active_row
                else:
                    cur.execute(
                        """
                        UPDATE public.admin_users
                        SET is_active = %s
                        WHERE id = %s
                        RETURNING id::text, email, role, is_active, totp_enabled, last_login
                        """,
                        (is_active, admin_uuid),
                    )
                    u = cur.fetchone()
                    if not u:
                        return None, "Failed to update admin status"
                    updated_row = {
                        "admin_id": u[0],
                        "email": u[1],
                        "role": u[2],
                        "is_active": bool(u[3]),
                        "totp_enabled": bool(u[4]),
                        "last_login": u[5],
                        "previous_is_active": old_active,
                        "previous_role": updated_row.get("previous_role")
                        if updated_row
                        else old_role,
                    }
        except Exception as exc:
            logger.exception("update_admin_staff is_active failed: %s", exc)
            return None, "Failed to update admin status"

    if updated_row is None:
        return None, "Admin user not found"

    if "previous_is_active" not in updated_row:
        updated_row["previous_is_active"] = updated_row.get("is_active")
    if "previous_role" not in updated_row:
        updated_row["previous_role"] = updated_row.get("role")

    return updated_row, None


def delete_admin_user(admin_id: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Permanently remove an admin_users row.

    Returns (deleted_row_dict, error_message).
    """
    try:
        admin_uuid = str(__import__("uuid").UUID(admin_id))
    except ValueError:
        return None, "Invalid admin_id"

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT email, role, is_active, totp_enabled
                FROM public.admin_users
                WHERE id = %s
                """,
                (admin_uuid,),
            )
            row = cur.fetchone()
            if row is None:
                return None, "Admin user not found"

            email, role, is_active, totp_enabled = row[0], row[1], bool(row[2]), bool(row[3])

            if role == "owner":
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM public.admin_users
                    WHERE role = 'owner' AND id <> %s
                    """,
                    (admin_uuid,),
                )
                other_owners = int(cur.fetchone()[0] or 0)
                if other_owners < 1:
                    return None, "Cannot delete the last owner account"

            cur.execute(
                """
                DELETE FROM public.admin_users
                WHERE id = %s
                RETURNING id::text
                """,
                (admin_uuid,),
            )
            deleted = cur.fetchone()
            if not deleted:
                return None, "Failed to delete admin user"

            return {
                "admin_id": deleted[0],
                "email": email,
                "role": role,
                "is_active": is_active,
                "totp_enabled": totp_enabled,
            }, None
    except Exception as exc:
        err = str(exc).lower()
        if "foreign key" in err or "violates" in err:
            return (
                None,
                "Cannot delete: this admin has audit history. Deactivate instead.",
            )
        logger.exception("delete_admin_user failed admin=%s: %s", admin_id, exc)
        return None, "Failed to delete admin user"


def generate_totp_secret() -> str:
    """
    Generate a new TOTP secret (base32 encoded).
    
    Admin must scan the resulting QR code with an authenticator app.
    
    Returns:
        Base32 TOTP secret string
    """
    import pyotp
    return pyotp.random_base32()


def set_totp_secret(email: str, totp_secret: str) -> Tuple[bool, Optional[str]]:
    """
    Store a TOTP secret for an admin and enable 2FA.
    
    Args:
        email: Admin email
        totp_secret: Base32 TOTP secret (from generate_totp_secret())
    
    Returns:
        Tuple[success, error_message]
    """
    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.admin_users
                SET totp_secret = %s, totp_enabled = TRUE
                WHERE email = %s
                """,
                (totp_secret, email),
            )
            logger.info("Updated TOTP secret for admin: %s", email)
            return True, None
    except Exception as exc:
        logger.exception("set_totp_secret failed: %s", exc)
        return False, "Failed to set TOTP secret"


def get_totp_provisioning_uri(email: str, issuer: str = "Sentient") -> Optional[str]:
    """
    Generate a provisioning URI for TOTP QR code.
    
    Args:
        email: Admin email (display name)
        issuer: Issuer name for authenticator app
    
    Returns:
        otpauth:// URI for QR code generation
    """
    try:
        totp_secret = generate_totp_secret()
        import pyotp
        totp = pyotp.TOTP(totp_secret)
        uri = totp.provisioning_uri(name=email, issuer_name=issuer)
        return uri
    except Exception as exc:
        logger.exception("get_totp_provisioning_uri failed: %s", exc)
        return None
