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

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            if SMTP_PORT == 587:
                server.starttls()
                server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Admin invite email sent to %s", to_email)
        return True, None
    except Exception as exc:
        logger.exception("Failed to send admin invite email to %s: %s", to_email, exc)
        return False, str(exc)


def build_admin_invite_url(plain_token: str) -> str:
    base = ADMIN_UI_URL.rstrip("/")
    return f"{base}?setup_token={plain_token}"
