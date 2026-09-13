"""Hardening Phase 3: /chat/stream must generate one model reply, not two."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.auth import get_chat_user
from app.main import app


def test_chat_stream_llm_path_calls_stream_once_not_full_generate() -> None:
    """Non-scripted stream must not call produce_reply / generate_reply first."""

    def fake_user():
        return {"sub": "00000000-0000-0000-0000-000000000001"}

    store = MagicMock()
    store.get_history.return_value = []

    app.dependency_overrides[get_chat_user] = fake_user
    try:
        with patch("app.main.chat_store", store), patch(
            "app.chat_service.check_quota", return_value=True
        ), patch(
            "app.chat_service.try_scripted_reply", return_value=None
        ), patch(
            "app.main.try_scripted_reply", return_value=None
        ), patch(
            "app.chat_service.generate_reply"
        ) as mock_generate, patch(
            "app.chat_service.generate_reply_stream",
            return_value=iter(["Hello", " there"]),
        ) as mock_stream:
            client = TestClient(app)
            response = client.post(
                "/chat/stream",
                json={"session_id": "stream-s1", "message": "hi"},
            )
            assert response.status_code == 200
            body = response.text
            assert "Hello" in body
            assert '"type": "done"' in body or '"type":"done"' in body

            mock_generate.assert_not_called()
            mock_stream.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_chat_stream_scripted_path_skips_llm() -> None:
    def fake_user():
        return {"sub": "00000000-0000-0000-0000-000000000001"}

    store = MagicMock()
    store.get_history.return_value = []

    app.dependency_overrides[get_chat_user] = fake_user
    try:
        with patch("app.main.chat_store", store), patch(
            "app.chat_service.check_quota", return_value=True
        ), patch(
            "app.chat_service.check_entitlement", return_value=True
        ), patch(
            "app.main.try_scripted_reply", return_value="Scripted coach line."
        ), patch(
            "app.chat_service.generate_reply"
        ) as mock_generate, patch(
            "app.chat_service.generate_reply_stream"
        ) as mock_stream:
            client = TestClient(app)
            response = client.post(
                "/chat/stream",
                json={
                    "session_id": "stream-s2",
                    "message": "hi",
                    "course_slug": "week-zero-reset",
                    "day_number": 1,
                },
            )
            assert response.status_code == 200
            assert "Scripted coach line" in response.text

            mock_generate.assert_not_called()
            mock_stream.assert_not_called()
    finally:
        app.dependency_overrides.clear()
