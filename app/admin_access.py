"""
admin_access.py — Network restriction for /admin/* (A7).

Set ADMIN_ALLOWED_IPS to a comma-separated allowlist. When unset, all IPs are
allowed (local dev). GET /admin/health is exempt from the allowlist check.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Request, status

from app.config import logger

_ADMIN_HEALTH_PATH = "/admin/health"
# Auth + UI must stay reachable when allowlist is set (remote admins need login + invite setup).
_ADMIN_IP_EXEMPT_PREFIXES = (
    "/admin/health",
    "/admin/ui",
    "/admin/auth/",
)


def admin_allowed_ips() -> frozenset[str]:
    """Read allowlist each call so .env changes apply without process restart."""
    raw = os.getenv("ADMIN_ALLOWED_IPS", "").strip()
    if not raw:
        return frozenset()
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


def client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def enforce_admin_ip(request: Request) -> None:
    """Raise 403 when ADMIN_ALLOWED_IPS is set and client IP is not listed."""
    allowed = admin_allowed_ips()
    if not allowed:
        return

    path = request.url.path
    if path == _ADMIN_HEALTH_PATH or any(
        path.startswith(prefix) for prefix in _ADMIN_IP_EXEMPT_PREFIXES
    ):
        return

    ip = client_ip(request)
    if ip and ip in allowed:
        return

    logger.warning(
        "Admin IP denied path=%s ip=%s allowlist_size=%d",
        path,
        ip,
        len(allowed),
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access restricted by IP",
    )


def is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")
