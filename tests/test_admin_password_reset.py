from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app import admin_password_reset as reset_mod


def test_mask_email():
    assert reset_mod._mask_email("ops@sentient.app") == "o***@sentient.app"
    assert reset_mod._mask_email("a@b.co") == "*@b.co"


def test_request_password_reset_not_found(monkeypatch):
    monkeypatch.setattr(reset_mod, "SUPABASE_DB_URL", "postgres://x")
    monkeypatch.setattr(reset_mod, "_ensure_reset_schema", lambda: True)

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    monkeypatch.setattr(reset_mod, "_get_db_connection", lambda: mock_cm)

    admin_id, token, found = reset_mod.request_password_reset("missing@example.com")
    assert admin_id is None
    assert token is None
    assert found is False


def test_request_password_reset_found(monkeypatch):
    monkeypatch.setattr(reset_mod, "SUPABASE_DB_URL", "postgres://x")
    monkeypatch.setattr(reset_mod, "_ensure_reset_schema", lambda: True)

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = ("admin-uuid-1",)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    monkeypatch.setattr(reset_mod, "_get_db_connection", lambda: mock_cm)

    admin_id, token, found = reset_mod.request_password_reset("ops@sentient.app")
    assert admin_id == "admin-uuid-1"
    assert token and len(token) > 20
    assert found is True


def test_complete_password_reset_invalid_totp(monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    plain = "test-reset-token-value-1234567890"
    token_hash = reset_mod._hash_token(plain)

    row = {
        "admin_id": "admin-1",
        "email": "ops@sentient.app",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "totp_enabled": True,
        "reset_expires_at": future,
        "setup_completed_at": future,
        "is_active": True,
    }

    monkeypatch.setattr(reset_mod, "_load_reset_row", lambda t: row if t == plain else None)

    ok, err, admin_id = reset_mod.complete_password_reset(plain, "newpassword1", "000000")
    assert ok is False
    assert "authenticator" in (err or "").lower()
    assert admin_id is None

