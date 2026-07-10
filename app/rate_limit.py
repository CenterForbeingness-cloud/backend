"""IP-level rate limiting via slowapi."""

from __future__ import annotations

from typing import Callable, TypeVar

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import (
    MARKETING_BEACON_SECRET,
    RATE_LIMIT_MARKETING,
    RATE_LIMIT_AUTH,
    RATE_LIMIT_BILLING,
    RATE_LIMIT_CHAT,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_SESSIONS,
)

F = TypeVar("F", bound=Callable)


def get_client_ip(request: Request) -> str:
    """Prefer the first X-Forwarded-For hop (Railway/proxy) over the socket address."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return get_remote_address(request)


limiter = Limiter(key_func=get_client_ip, enabled=RATE_LIMIT_ENABLED)


def rate_limit(limit: str) -> Callable[[F], F]:
    """Apply a slowapi limit when RATE_LIMIT_ENABLED=true; otherwise no-op."""
    if not RATE_LIMIT_ENABLED:
        def passthrough(func: F) -> F:
            return func

        return passthrough
    return limiter.limit(limit)


CHAT_LIMIT = rate_limit(RATE_LIMIT_CHAT)
SESSIONS_LIMIT = rate_limit(RATE_LIMIT_SESSIONS)
AUTH_LIMIT = rate_limit(RATE_LIMIT_AUTH)
BILLING_LIMIT = rate_limit(RATE_LIMIT_BILLING)
MARKETING_LIMIT = rate_limit(RATE_LIMIT_MARKETING)
