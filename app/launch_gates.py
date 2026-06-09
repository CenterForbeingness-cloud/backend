"""
launch_gates.py — Track A production readiness checks (env only, no secrets in output).
"""

from __future__ import annotations

from dataclasses import dataclass
from app.config import (
    AUTH_ENFORCED,
    CHAT_TOKEN_ENFORCED,
    CHAT_TOKEN_SECRET,
    CORS_ORIGINS,
    RATE_LIMIT_ENABLED,
    STRIPE_PRICE_BY_COURSE_SLUG,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    SUPABASE_DB_URL,
    SUPABASE_JWT_SECRET,
)


@dataclass(frozen=True)
class LaunchGateCheck:
    gate_id: str
    name: str
    ok: bool
    detail: str
    required: bool = True


def _stripe_mode() -> str:
    key = (STRIPE_SECRET_KEY or "").strip()
    if key.startswith("sk_live_"):
        return "live"
    if key.startswith("sk_test_"):
        return "test"
    return "unset"


def evaluate_launch_gates(*, strict_live: bool = False) -> dict:
    """
    Evaluate Track A env gates. Returns summary dict safe to expose on /health/launch-gates.
    """
    checks: list[LaunchGateCheck] = []

    checks.append(
        LaunchGateCheck(
            "A2",
            "AUTH_ENFORCED",
            AUTH_ENFORCED,
            "true" if AUTH_ENFORCED else "set AUTH_ENFORCED=true on Railway",
        )
    )
    checks.append(
        LaunchGateCheck(
            "A3a",
            "CHAT_TOKEN_ENFORCED",
            CHAT_TOKEN_ENFORCED,
            "true" if CHAT_TOKEN_ENFORCED else "set CHAT_TOKEN_ENFORCED=true",
        )
    )
    chat_secret_ok = bool((CHAT_TOKEN_SECRET or "").strip())
    checks.append(
        LaunchGateCheck(
            "A3b",
            "CHAT_TOKEN_SECRET",
            chat_secret_ok or not CHAT_TOKEN_ENFORCED,
            "configured" if chat_secret_ok else "set CHAT_TOKEN_SECRET or SUPABASE_JWT_SECRET",
            required=CHAT_TOKEN_ENFORCED,
        )
    )
    checks.append(
        LaunchGateCheck(
            "A4",
            "RATE_LIMIT_ENABLED",
            RATE_LIMIT_ENABLED,
            "true" if RATE_LIMIT_ENABLED else "set RATE_LIMIT_ENABLED=true",
        )
    )
    cors_ok = bool(CORS_ORIGINS)
    checks.append(
        LaunchGateCheck(
            "A6",
            "CORS_ORIGINS",
            cors_ok,
            f"{len(CORS_ORIGINS)} origin(s)" if cors_ok else "comma-separated origins, no wildcard",
        )
    )
    jwt_ok = bool((SUPABASE_JWT_SECRET or "").strip())
    checks.append(
        LaunchGateCheck(
            "A2b",
            "SUPABASE_JWT_SECRET",
            jwt_ok or not AUTH_ENFORCED,
            "configured" if jwt_ok else "required when AUTH_ENFORCED=true",
            required=AUTH_ENFORCED,
        )
    )
    db_ok = bool((SUPABASE_DB_URL or "").strip())
    checks.append(
        LaunchGateCheck(
            "A11a",
            "SUPABASE_DB_URL",
            db_ok,
            "configured" if db_ok else "Postgres connection string required",
        )
    )

    stripe_key = (STRIPE_SECRET_KEY or "").strip()
    stripe_configured = bool(stripe_key)
    mode = _stripe_mode()
    checks.append(
        LaunchGateCheck(
            "A8a",
            "STRIPE_SECRET_KEY",
            stripe_configured,
            mode if stripe_configured else "sk_live_... or sk_test_... for rehearsal",
        )
    )
    webhook_ok = bool((STRIPE_WEBHOOK_SECRET or "").strip())
    checks.append(
        LaunchGateCheck(
            "A5",
            "STRIPE_WEBHOOK_SECRET",
            webhook_ok or not stripe_configured,
            "configured" if webhook_ok else "Dashboard whsec_... (not CLI secret in prod)",
            required=stripe_configured,
        )
    )
    if strict_live and stripe_configured:
        checks.append(
            LaunchGateCheck(
                "A8b",
                "STRIPE_LIVE_MODE",
                mode == "live",
                f"current mode: {mode}",
            )
        )

    price_count = len(STRIPE_PRICE_BY_COURSE_SLUG)
    checks.append(
        LaunchGateCheck(
            "A9",
            "STRIPE_PRICE_BY_COURSE",
            price_count > 0,
            f"{price_count} course price(s) from env" if price_count else "set STRIPE_PRICE_* vars",
        )
    )

    required_checks = [c for c in checks if c.required]
    blocking = [f"{c.gate_id} {c.name}" for c in required_checks if not c.ok]
    ready = len(blocking) == 0

    return {
        "ready": ready,
        "track": "A",
        "stripe_mode": mode,
        "blocking": blocking,
        "checks": [
            {
                "gate_id": c.gate_id,
                "name": c.name,
                "ok": c.ok,
                "detail": c.detail,
                "required": c.required,
            }
            for c in checks
        ],
    }
