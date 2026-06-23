"""Tests for GET /usage including voice quota fields."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app

client = TestClient(app)


def test_usage_includes_voice_fields():
    def fake_user():
        return {"sub": "00000000-0000-0000-0000-000000000001"}

    app.dependency_overrides[get_current_user] = fake_user
    try:
        with patch("app.main.get_usage_info") as mock_messages, patch(
            "app.main.get_voice_usage_info"
        ) as mock_voice:
            mock_messages.return_value = {
                "messages_today": 12,
                "limit": 100,
                "reset_at": "2026-06-24T12:00:00+00:00",
            }
            mock_voice.return_value = {
                "voice_seconds_today": 90.5,
                "voice_seconds_limit": 600,
                "voice_reset_at": "2026-06-24T00:00:00+00:00",
            }

            response = client.get(
                "/usage", headers={"Authorization": "Bearer test"}
            )

            assert response.status_code == 200
            body = response.json()
            assert body["messages_today"] == 12
            assert body["limit"] == 100
            assert body["voice_seconds_today"] == 90.5
            assert body["voice_seconds_limit"] == 600
            assert body["voice_reset_at"] is not None
    finally:
        app.dependency_overrides.clear()


def test_usage_requires_auth():
    def no_user():
        return None

    app.dependency_overrides[get_current_user] = no_user
    try:
        response = client.get("/usage", headers={"Authorization": "Bearer test"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
