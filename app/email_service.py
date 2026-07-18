"""
email_service.py — All transactional email via the Resend HTTP API.

Used for:
  - Admin staff invites and admin password reset
  - App auth emails (signup confirm, recovery, etc.) via the Supabase Send Email hook
"""

from __future__ import annotations

from typing import Optional

import httpx

from app.config import (
    ADMIN_UI_URL,
    RESEND_API_KEY,
    RESEND_FROM_EMAIL,
    logger,
)

RESEND_API_URL = "https://api.resend.com/emails"


def resend_configured() -> bool:
    return bool(RESEND_API_KEY and RESEND_FROM_EMAIL)


def send_email(
    *,
    to_email: str,
    subject: str,
    text: str,
    html: str,
) -> tuple[bool, Optional[str]]:
    """
    Send one email through Resend. Returns (sent, error_or_none).
    When Resend is not configured, returns (False, None) so callers can fall back
    (e.g. show the link in the admin UI).
    """
    to = to_email.strip()
    if not to:
        return False, "Missing recipient"

    if not RESEND_FROM_EMAIL:
        logger.warning(
            "Email not sent (RESEND_FROM_EMAIL missing). to=%s subject=%s",
            to,
            subject,
        )
        return False, None

    if not RESEND_API_KEY:
        logger.warning(
            "Email not sent (RESEND_API_KEY missing). to=%s subject=%s",
            to,
            subject,
        )
        return False, "RESEND_API_KEY not configured"

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "text": text,
        "html": html,
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(RESEND_API_URL, json=payload, headers=headers)
        if resp.status_code >= 400:
            detail = resp.text[:500]
            logger.error(
                "Resend API error status=%s to=%s body=%s",
                resp.status_code,
                to,
                detail,
            )
            return False, f"Resend HTTP {resp.status_code}: {detail}"
        logger.info("Email sent via Resend to %s subject=%s", to, subject)
        return True, None
    except Exception as exc:
        logger.exception("Failed to send email via Resend to %s: %s", to, exc)
        return False, str(exc)


def send_admin_invite_email(
    to_email: str,
    invite_url: str,
    role: str,
) -> tuple[bool, Optional[str]]:
    """
    Send admin invite email. Returns (sent, error_or_none).
    When Resend is not configured, returns (False, None) and logs the link.
    """
    subject = "You're invited to Sentient Admin"
    body = f"""Hello,

You've been invited as a Sentient admin ({role} role).

Open this link to set your password and connect your authenticator app (Google Authenticator, Authy, etc.):

{invite_url}

This link expires in 72 hours. If you did not expect this email, ignore it.

— Sentient Ops
"""
    html = f"""<p>Hello,</p>
<p>You've been invited as a Sentient admin (<strong>{role}</strong> role).</p>
<p><a href="{invite_url}">Set up your admin account and 2FA</a></p>
<p>Or copy this URL:<br/><code>{invite_url}</code></p>
<p>This link expires in 72 hours.</p>
<p>— Sentient Ops</p>"""

    if not resend_configured():
        logger.warning(
            "Admin invite email not sent (Resend not configured). Invite URL for %s: %s",
            to_email,
            invite_url,
        )
        return False, None if not RESEND_FROM_EMAIL else "RESEND_API_KEY not configured"

    return send_email(to_email=to_email, subject=subject, text=body, html=html)


def build_admin_invite_url(plain_token: str) -> str:
    base = ADMIN_UI_URL.rstrip("/")
    return f"{base}?setup_token={plain_token}"


def build_admin_password_reset_url(plain_token: str) -> str:
    base = ADMIN_UI_URL.rstrip("/")
    return f"{base}?reset_token={plain_token}"


def send_admin_password_reset_email(
    to_email: str,
    reset_url: str,
) -> tuple[bool, Optional[str]]:
    """Send password reset email. Returns (sent, error_or_none)."""
    subject = "Reset your Sentient Admin password"
    body = f"""Hello,

We received a request to reset the password for your Sentient admin account.

Open this link to choose a new password (you will need your authenticator app):

{reset_url}

This link expires in 24 hours. If you did not request this, ignore this email.

— Sentient Ops
"""
    html = f"""<p>Hello,</p>
<p>We received a request to reset the password for your Sentient admin account.</p>
<p><a href="{reset_url}">Reset your password</a></p>
<p>Or copy this URL:<br/><code>{reset_url}</code></p>
<p>You will need your existing authenticator app (6-digit code) to finish.</p>
<p>This link expires in 24 hours.</p>
<p>— Sentient Ops</p>"""

    if not resend_configured():
        logger.warning(
            "Admin password reset email not sent (Resend not configured). Reset URL for %s: %s",
            to_email,
            reset_url,
        )
        return False, None if not RESEND_FROM_EMAIL else "RESEND_API_KEY not configured"

    return send_email(to_email=to_email, subject=subject, text=body, html=html)


def build_auth_action_email(
    *,
    email_action_type: str,
    confirm_url: str,
    token: str = "",
) -> tuple[str, str, str]:
    """
    Build (subject, text, html) for a Supabase Auth email action.
    """
    action = (email_action_type or "").strip().lower()
    otp_line = ""
    otp_html = ""
    if token:
        otp_line = f"\nOr enter this code: {token}\n"
        otp_html = f"<p>Or enter this code: <strong>{token}</strong></p>"

    if action in {"signup", "email_confirmation", "confirm"}:
        subject = "Confirm your Sentient email"
        cta = "Confirm your email"
        intro = "Thanks for signing up for Sentient. Confirm your email to finish creating your account."
    elif action in {"recovery", "reset_password"}:
        subject = "Reset your Sentient password"
        cta = "Reset your password"
        intro = "We received a request to reset your Sentient password."
    elif action == "magiclink":
        subject = "Your Sentient sign-in link"
        cta = "Sign in to Sentient"
        intro = "Use this link to sign in to Sentient."
    elif action == "invite":
        subject = "You're invited to Sentient"
        cta = "Accept invite"
        intro = "You've been invited to Sentient."
    elif action == "email_change":
        subject = "Confirm your new Sentient email"
        cta = "Confirm email change"
        intro = "Confirm this address to finish updating your Sentient email."
    elif action == "reauthentication":
        subject = "Confirm it's you — Sentient"
        cta = "Confirm"
        intro = "Confirm this action on your Sentient account."
    else:
        subject = "Sentient account action"
        cta = "Continue"
        intro = "Complete this action for your Sentient account."

    text = f"""Hello,

{intro}

{cta}:
{confirm_url}
{otp_line}
If you did not request this, you can ignore this email.

— Sentient
"""
    html = f"""<p>Hello,</p>
<p>{intro}</p>
<p><a href="{confirm_url}">{cta}</a></p>
<p>Or copy this URL:<br/><code>{confirm_url}</code></p>
{otp_html}
<p>If you did not request this, you can ignore this email.</p>
<p>— Sentient</p>"""
    return subject, text, html


def send_auth_action_email(
    *,
    to_email: str,
    email_action_type: str,
    confirm_url: str,
    token: str = "",
) -> tuple[bool, Optional[str]]:
    """Send a Supabase Auth action email (signup, recovery, etc.) via Resend."""
    subject, text, html = build_auth_action_email(
        email_action_type=email_action_type,
        confirm_url=confirm_url,
        token=token,
    )
    if not resend_configured():
        logger.warning(
            "Auth email not sent (Resend not configured). type=%s to=%s url=%s",
            email_action_type,
            to_email,
            confirm_url,
        )
        return False, "Resend not configured"
    return send_email(to_email=to_email, subject=subject, text=text, html=html)
