"""
email_service.py — transactional email (admin invites). SMTP via env vars.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Optional

from app.config import (
    ADMIN_INVITE_FROM_EMAIL,
    ADMIN_UI_URL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    logger,
)

_SSL_PORTS = frozenset({465, 2465})
_STARTTLS_PORTS = frozenset({25, 587, 2587})


def _smtp_login_user() -> str:
    """Resend SMTP username is always the literal string ``resend``."""
    if SMTP_USER:
        return SMTP_USER
    if "resend.com" in SMTP_HOST.lower():
        return "resend"
    return ""


def _send_via_smtp(msg: EmailMessage) -> None:
    login_user = _smtp_login_user()
    if SMTP_PORT in _SSL_PORTS:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            if login_user and SMTP_PASSWORD:
                server.login(login_user, SMTP_PASSWORD)
            server.send_message(msg)
        return

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.ehlo()
        if SMTP_PORT in _STARTTLS_PORTS:
            server.starttls()
            server.ehlo()
        if login_user and SMTP_PASSWORD:
            server.login(login_user, SMTP_PASSWORD)
        server.send_message(msg)


def send_admin_invite_email(
    to_email: str,
    invite_url: str,
    role: str,
) -> tuple[bool, Optional[str]]:
    """
    Send admin invite email. Returns (sent, error_or_none).
    When SMTP is not configured, returns (False, None) and logs the link.
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

    if not SMTP_HOST or not ADMIN_INVITE_FROM_EMAIL:
        logger.warning(
            "Admin invite email not sent (SMTP not configured). Invite URL for %s: %s",
            to_email,
            invite_url,
        )
        return False, None

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = ADMIN_INVITE_FROM_EMAIL
    msg["To"] = to_email.strip()
    msg.set_content(body)
    msg.add_alternative(html, subtype="html")

    if not SMTP_PASSWORD:
        logger.warning(
            "Admin invite email not sent (SMTP_PASSWORD missing). Invite URL for %s: %s",
            to_email,
            invite_url,
        )
        return False, "SMTP_PASSWORD not configured"

    try:
        _send_via_smtp(msg)
        logger.info("Admin invite email sent to %s", to_email)
        return True, None
    except Exception as exc:
        logger.exception("Failed to send admin invite email to %s: %s", to_email, exc)
        return False, str(exc)


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

    if not SMTP_HOST or not ADMIN_INVITE_FROM_EMAIL:
        logger.warning(
            "Admin password reset email not sent (SMTP not configured). Reset URL for %s: %s",
            to_email,
            reset_url,
        )
        return False, None

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = ADMIN_INVITE_FROM_EMAIL
    msg["To"] = to_email.strip()
    msg.set_content(body)
    msg.add_alternative(html, subtype="html")

    if not SMTP_PASSWORD:
        logger.warning(
            "Admin password reset email not sent (SMTP_PASSWORD missing). Reset URL for %s: %s",
            to_email,
            reset_url,
        )
        return False, "SMTP_PASSWORD not configured"

    try:
        _send_via_smtp(msg)
        logger.info("Admin password reset email sent to %s", to_email)
        return True, None
    except Exception as exc:
        logger.exception("Failed to send password reset email to %s: %s", to_email, exc)
        return False, str(exc)
