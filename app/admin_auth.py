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


def create_admin_user(email: str, password: str, role: str) -> Tuple[bool, Optional[str]]:
    """
    Create a new admin user with bcrypt-hashed password.
    TOTP secret must be generated separately via generate_totp_secret().
    
    Args:
        email: Admin email address
        password: Admin password (plaintext, will be hashed)
        role: Admin role ('owner', 'editor', 'viewer')
    
    Returns:
        Tuple[success, error_message]
    """
    try:
        import bcrypt
        
        # Hash password with bcrypt
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.admin_users (email, password_hash, role, is_active)
                VALUES (%s, %s, %s, TRUE)
                """,
                (email, password_hash, role),
            )
            logger.info("Created admin user: %s role=%s", email, role)
            return True, None
    except Exception as exc:
        if "unique constraint" in str(exc).lower():
            logger.warning("Admin user already exists: %s", email)
            return False, "Email already registered as admin"
        logger.exception("create_admin_user failed: %s", exc)
        return False, "Failed to create admin user"


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
