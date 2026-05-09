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
    from app.config import AUTH_ENFORCED, SUPABASE_JWT_SECRET, SUPABASE_URL

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
