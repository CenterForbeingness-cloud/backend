from typing import Optional

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_security),
) -> Optional[dict]:
    """
    Verify Supabase JWT.

    If SUPABASE_JWT_SECRET is not set the backend runs in dev mode and allows
    all requests through (returns None). Set the secret in .env to enforce auth.
    """
    from app.config import AUTH_ENFORCED, SUPABASE_JWT_SECRET

    if not AUTH_ENFORCED:
        return None

    if not SUPABASE_JWT_SECRET:
        return None

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        import jwt

        payload = jwt.decode(
            credentials.credentials,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
