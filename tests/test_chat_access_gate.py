"""Server chat gate: trial or subscription required for chat."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import get_chat_user
from app.main import app
from app.user_access import UserAccess, require_chat_access


def test_require_chat_access_allows_missing_user():
    access = require_chat_access(None)
    assert access.chat_allowed is False


def test_require_chat_access_allows_without_database_url():
    with patch("app.user_access.SUPABASE_DB_URL", None):
        access = require_chat_access("user-1")
    assert access.user_id == "user-1"


def test_require_chat_access_blocks_before_trial():
    blocked = UserAccess(user_id="user-1")
    with patch("app.user_access.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.user_access.get_user_access", return_value=blocked
    ):
        with pytest.raises(HTTPException) as exc_info:
            require_chat_access("user-1")
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert detail["code"] == "trial_required"
    assert detail["chat_allowed"] is False


def test_require_chat_access_blocks_after_expired_trial():
    now = datetime.now(timezone.utc)
    expired = UserAccess(
        user_id="user-1",
        trial_started_at=now - timedelta(days=20),
        trial_ends_at=now - timedelta(days=6),
    )
    with patch("app.user_access.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.user_access.get_user_access", return_value=expired
    ):
        with pytest.raises(HTTPException) as exc_info:
            require_chat_access("user-1")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "subscription_required"


def test_require_chat_access_allows_active_trial():
    now = datetime.now(timezone.utc)
    active = UserAccess(
        user_id="user-1",
        trial_started_at=now - timedelta(days=1),
        trial_ends_at=now + timedelta(days=13),
    )
    with patch("app.user_access.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.user_access.get_user_access", return_value=active
    ):
        access = require_chat_access("user-1")
    assert access.chat_allowed is True


def test_require_chat_access_allows_subscription():
    subscribed = UserAccess(user_id="user-1", subscription_active=True)
    with patch("app.user_access.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.user_access.get_user_access", return_value=subscribed
    ):
        access = require_chat_access("user-1")
    assert access.chat_allowed is True


def test_chat_endpoint_returns_trial_required():
    def fake_user():
        return {"sub": "user-gate-1"}

    blocked = UserAccess(user_id="user-gate-1")
    app.dependency_overrides[get_chat_user] = fake_user
    try:
        with patch("app.user_access.SUPABASE_DB_URL", "postgresql://example"), patch(
            "app.user_access.get_user_access", return_value=blocked
        ), patch("app.chat_service.check_quota", return_value=True):
            client = TestClient(app)
            response = client.post(
                "/chat",
                json={"session_id": "gate-s1", "message": "hello"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "trial_required"


def test_media_playback_not_blocked_by_trial_gate():
    """Meditate in 5 stays available before trial (always free)."""
    with patch(
        "app.mux_media.resolve_playback",
        return_value={
            "content_key": "meditate-in-5",
            "title": "Meditate in 5 Minutes",
            "playback_id": "pb123",
            "playback_url": "https://stream.mux.com/pb123.m3u8",
            "signed": False,
            "host": "mux",
        },
    ):
        client = TestClient(app)
        response = client.get("/media/meditate-in-5/playback")

    assert response.status_code == 200
    assert response.json()["playback_url"].startswith("https://stream.mux.com/")
