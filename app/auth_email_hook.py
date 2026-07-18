"""
auth_email_hook.py — Supabase Auth Send Email Hook → Resend API.

Configure in Supabase Dashboard:
  Authentication → Hooks → Send Email Hook
  URL: https://<your-api-host>/hooks/supabase/send-email
  Secret: paste into SUPABASE_SEND_EMAIL_HOOK_SECRET

When enabled, Supabase stops sending auth emails itself and POSTs here instead.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode

from app.config import SUPABASE_SEND_EMAIL_HOOK_SECRET, SUPABASE_URL, logger
from app.email_service import send_auth_action_email


_MAX_TIMESTAMP_AGE_SEC = 5 * 60


def _parse_hook_secret(raw: str) -> bytes:
    """Decode Supabase/Standard Webhooks secret (v1,whsec_... or whsec_...)."""
    secret = (raw or "").strip()
    if secret.startswith("v1,"):
        secret = secret[3:].strip()
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_") :]
    return base64.b64decode(secret)


def verify_supabase_hook_signature(
    body: bytes,
    *,
    webhook_id: str | None,
    webhook_timestamp: str | None,
    webhook_signature: str | None,
    secret: str | None = None,
    now: Optional[float] = None,
) -> bool:
    """
    Verify Standard Webhooks signature used by Supabase Auth Hooks.
    Returns False if secret is unset or signature is invalid.
    """
    secret_raw = (secret if secret is not None else SUPABASE_SEND_EMAIL_HOOK_SECRET) or ""
    if not secret_raw:
        return False
    if not webhook_id or not webhook_timestamp or not webhook_signature:
        return False

    try:
        ts = int(webhook_timestamp)
    except ValueError:
        return False

    current = time.time() if now is None else now
    if abs(current - ts) > _MAX_TIMESTAMP_AGE_SEC:
        logger.warning("Supabase email hook timestamp out of range: %s", webhook_timestamp)
        return False

    try:
        key = _parse_hook_secret(secret_raw)
    except Exception:
        logger.exception("Invalid SUPABASE_SEND_EMAIL_HOOK_SECRET encoding")
        return False

    to_sign = f"{webhook_id}.{webhook_timestamp}.".encode("utf-8") + body
    digest = hmac.new(key, to_sign, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")

    candidates: list[str] = []
    for part in webhook_signature.replace(",", " ").split():
        part = part.strip()
        if part.startswith("v1,"):
            candidates.append(part[3:])
        elif part.startswith("v1="):
            candidates.append(part[3:])
        elif "," in part:
            version, _, sig = part.partition(",")
            if version == "v1" and sig:
                candidates.append(sig)
        else:
            candidates.append(part)

    return any(hmac.compare_digest(expected, c) for c in candidates if c)


def build_confirm_url(
    *,
    email_data: dict[str, Any],
    supabase_url: str | None = None,
) -> str:
    """
    Build the Auth verify URL Supabase expects users to open.
    https://supabase.com/docs/guides/auth/auth-hooks/send-email-hook
    """
    base = (supabase_url if supabase_url is not None else SUPABASE_URL) or ""
    base = base.rstrip("/")
    token_hash = str(email_data.get("token_hash") or "")
    action_type = str(email_data.get("email_action_type") or "signup")
    redirect_to = str(email_data.get("redirect_to") or "")

    if not base or not token_hash:
        site_url = str(email_data.get("site_url") or "").rstrip("/")
        if site_url and token_hash:
            base = site_url
        else:
            return redirect_to or ""

    query = {
        "token": token_hash,
        "type": action_type,
    }
    if redirect_to:
        query["redirect_to"] = redirect_to
    return f"{base}/auth/v1/verify?{urlencode(query, quote_via=quote)}"


def handle_send_email_payload(payload: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Process a verified Send Email Hook JSON body.
    Returns (ok, error_message).
    """
    user = payload.get("user") or {}
    email_data = payload.get("email_data") or {}
    if not isinstance(user, dict) or not isinstance(email_data, dict):
        return False, "Invalid hook payload shape"

    to_email = str(user.get("email") or "").strip()
    if not to_email:
        return False, "Missing user email"

    action_type = str(email_data.get("email_action_type") or "signup")
    token = str(email_data.get("token") or "")
    confirm_url = build_confirm_url(email_data=email_data)
    if not confirm_url:
        return False, "Could not build confirmation URL"

    sent, err = send_auth_action_email(
        to_email=to_email,
        email_action_type=action_type,
        confirm_url=confirm_url,
        token=token,
    )
    if not sent:
        return False, err or "Failed to send email"
    return True, None


def parse_hook_json(body: bytes) -> dict[str, Any]:
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Hook body must be a JSON object")
    return data
