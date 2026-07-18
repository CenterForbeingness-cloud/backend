"""Tests for Resend email service and Supabase Send Email Hook."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app import auth_email_hook as hook_mod
from app import email_service as email_mod


def _make_secret_pair() -> tuple[str, bytes]:
    raw = b"test-hook-secret-bytes-32chars!!"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"v1,whsec_{b64}", raw


def _sign(body: bytes, msg_id: str, ts: str, key: bytes) -> str:
    to_sign = f"{msg_id}.{ts}.".encode("utf-8") + body
    digest = hmac.new(key, to_sign, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def test_verify_supabase_hook_signature_ok():
    secret_env, key = _make_secret_pair()
    body = b'{"user":{"email":"a@b.co"},"email_data":{}}'
    msg_id = "msg_123"
    ts = str(int(time.time()))
    sig = _sign(body, msg_id, ts, key)

    assert hook_mod.verify_supabase_hook_signature(
        body,
        webhook_id=msg_id,
        webhook_timestamp=ts,
        webhook_signature=sig,
        secret=secret_env,
    )


def test_verify_supabase_hook_signature_rejects_bad_sig():
    secret_env, _key = _make_secret_pair()
    body = b'{"ok":true}'
    assert not hook_mod.verify_supabase_hook_signature(
        body,
        webhook_id="msg_1",
        webhook_timestamp=str(int(time.time())),
        webhook_signature="v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        secret=secret_env,
    )


def test_build_confirm_url():
    url = hook_mod.build_confirm_url(
        email_data={
            "token_hash": "abc123",
            "email_action_type": "signup",
            "redirect_to": "io.supabase.flutter://login-callback/",
        },
        supabase_url="https://proj.supabase.co",
    )
    assert url.startswith("https://proj.supabase.co/auth/v1/verify?")
    assert "token=abc123" in url
    assert "type=signup" in url
    assert "redirect_to=" in url


def test_build_auth_action_email_signup():
    subject, text, html = email_mod.build_auth_action_email(
        email_action_type="signup",
        confirm_url="https://example.com/confirm",
        token="123456",
    )
    assert "Confirm" in subject
    assert "https://example.com/confirm" in text
    assert "123456" in html


def test_send_email_calls_resend(monkeypatch):
    monkeypatch.setattr(email_mod, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(email_mod, "RESEND_FROM_EMAIL", "Sentient <ops@example.com>")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"id":"email_1"}'
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_resp

    with patch.object(email_mod.httpx, "Client", return_value=mock_client):
        ok, err = email_mod.send_email(
            to_email="user@example.com",
            subject="Hi",
            text="plain",
            html="<p>hi</p>",
        )

    assert ok is True
    assert err is None
    args, kwargs = mock_client.post.call_args
    assert args[0] == email_mod.RESEND_API_URL
    assert kwargs["json"]["to"] == ["user@example.com"]
    assert kwargs["headers"]["Authorization"] == "Bearer re_test"


def test_send_email_not_configured(monkeypatch):
    monkeypatch.setattr(email_mod, "RESEND_API_KEY", "")
    monkeypatch.setattr(email_mod, "RESEND_FROM_EMAIL", "Sentient <ops@example.com>")
    ok, err = email_mod.send_admin_invite_email(
        "ops@example.com",
        "https://admin/setup",
        "owner",
    )
    assert ok is False
    assert err == "RESEND_API_KEY not configured"


def test_handle_send_email_payload(monkeypatch):
    monkeypatch.setattr(hook_mod, "SUPABASE_URL", "https://proj.supabase.co")

    captured: dict = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return True, None

    monkeypatch.setattr(hook_mod, "send_auth_action_email", fake_send)

    ok, err = hook_mod.handle_send_email_payload(
        {
            "user": {"email": "new@example.com"},
            "email_data": {
                "token": "654321",
                "token_hash": "hashvalue",
                "email_action_type": "recovery",
                "redirect_to": "io.supabase.flutter://login-callback/",
            },
        }
    )
    assert ok is True
    assert err is None
    assert captured["to_email"] == "new@example.com"
    assert captured["email_action_type"] == "recovery"
    assert "token=hashvalue" in captured["confirm_url"]
    assert captured["token"] == "654321"
