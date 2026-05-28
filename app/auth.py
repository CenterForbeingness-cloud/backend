from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_security),
) -> Optional[dict]:
    """
    Verify Supabase JWT.

    When AUTH_ENFORCED=true, SUPABASE_JWT_SECRET must be configured.
    In local dev, keep AUTH_ENFORCED=false to allow unauthenticated requests.
    """
    from app.config import AUTH_ENFORCED, SUPABASE_JWT_SECRET, SUPABASE_URL

    if not AUTH_ENFORCED:
        return None

    if not SUPABASE_JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth is enforced but SUPABASE_JWT_SECRET is not configured",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        import jwt

        token = credentials.credentials
        header = jwt.get_unverified_header(token)
        alg = str(header.get("alg", "")).upper()

        if alg == "HS256":
            payload = jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        elif alg in {"RS256", "ES256", "ES384", "ES512"}:
            if not SUPABASE_URL:
                raise ValueError("SUPABASE_URL is required for asymmetric JWT verification")

            jwks_url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
            jwk_client = jwt.PyJWKClient(jwks_url)
            signing_key = jwk_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
            )
        else:
            raise ValueError(f"Unsupported JWT algorithm: {alg}")

        return payload
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def create_chat_token(user_id: str, plan: str = "free", session_id: Optional[str] = None) -> tuple[str, int]:
    from app.config import CHAT_TOKEN_SECRET, CHAT_TOKEN_TTL_SECONDS

    if not CHAT_TOKEN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat token secret is not configured",
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=CHAT_TOKEN_TTL_SECONDS)
    payload = {
        "sub": user_id,
        "scope": "chat",
        "plan": plan,
        "session_id": session_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    import jwt

    token = jwt.encode(payload, CHAT_TOKEN_SECRET, algorithm="HS256")
    return token, CHAT_TOKEN_TTL_SECONDS


def get_chat_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_security),
) -> Optional[dict]:
    from app.config import CHAT_TOKEN_ENFORCED, CHAT_TOKEN_SECRET

    if not CHAT_TOKEN_ENFORCED:
        return get_current_user(credentials)

    if not CHAT_TOKEN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat token secret is not configured",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chat token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        import jwt

        payload = jwt.decode(
            credentials.credentials,
            CHAT_TOKEN_SECRET,
            algorithms=["HS256"],
        )
        if payload.get("scope") != "chat":
            raise ValueError("Invalid chat token scope")
        if not payload.get("sub"):
            raise ValueError("Chat token missing subject")
        return payload
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired chat token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
