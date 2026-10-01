"""Unit tests for trial and subscription access helpers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.user_access import (
    TRIAL_DURATION_DAYS,
    UserAccess,
    access_to_response_dict,
    empty_user_access,
)


def test_empty_access_defaults():
    access = empty_user_access("u1")
    assert access.trial_started is False
    assert access.trial_active is False
    assert access.subscription_active is False
    assert access.chat_allowed is False


def test_trial_active_while_inside_window():
    now = datetime.now(timezone.utc)
    access = UserAccess(
        user_id="u1",
        trial_started_at=now - timedelta(days=1),
        trial_ends_at=now + timedelta(days=13),
    )
    assert access.trial_started is True
    assert access.trial_active is True
    assert access.chat_allowed is True


def test_trial_inactive_after_end():
    now = datetime.now(timezone.utc)
    access = UserAccess(
        user_id="u1",
        trial_started_at=now - timedelta(days=15),
        trial_ends_at=now - timedelta(days=1),
    )
    assert access.trial_started is True
    assert access.trial_active is False
    assert access.chat_allowed is False


def test_subscription_allows_chat_without_trial():
    access = UserAccess(user_id="u1", subscription_active=True)
    assert access.trial_active is False
    assert access.chat_allowed is True


def test_access_response_marks_meditate_in_5_always_free():
    payload = access_to_response_dict(empty_user_access("u1"))
    assert payload["meditate_in_5_free"] is True
    assert payload["trial_started"] is False
    assert payload["chat_allowed"] is False
    assert TRIAL_DURATION_DAYS == 14


def test_start_trial_is_idempotent_when_already_started():
    from app import user_access as mod

    existing = UserAccess(
        user_id="u1",
        trial_started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        trial_ends_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    with patch.object(mod, "SUPABASE_DB_URL", "postgresql://example"), patch.object(
        mod, "_ensure_access_schema", return_value=True
    ), patch.object(mod, "get_user_access", return_value=existing):
        result = mod.start_trial("u1")
    assert result.trial_started_at == existing.trial_started_at
    assert result.trial_ends_at == existing.trial_ends_at


def test_start_trial_writes_fourteen_day_window():
    from app import user_access as mod

    empty = empty_user_access("u1")
    now = datetime.now(timezone.utc)
    ends = now + timedelta(days=14)

    cursor = MagicMock()
    cursor.fetchone.return_value = (
        "u1",
        now,
        ends,
        False,
        None,
        now,
        now,
    )
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch.object(mod, "SUPABASE_DB_URL", "postgresql://example"), patch.object(
        mod, "_ensure_access_schema", return_value=True
    ), patch.object(mod, "get_user_access", return_value=empty), patch.object(
        mod, "_get_db_connection"
    ) as mock_conn:
        mock_conn.return_value.__enter__.return_value = conn
        result = mod.start_trial("u1")

    assert result.trial_started_at == now
    assert result.trial_ends_at == ends
    cursor.execute.assert_called_once()


def test_start_trial_requires_database():
    from app import user_access as mod

    with patch.object(mod, "SUPABASE_DB_URL", None):
        with pytest.raises(RuntimeError, match="SUPABASE_DB_URL"):
            mod.start_trial("u1")


def test_set_subscription_active_updates_flag():
    from app import user_access as mod

    now = datetime.now(timezone.utc)
    cursor = MagicMock()
    cursor.fetchone.return_value = (
        "u1",
        None,
        None,
        True,
        now,
        now,
        now,
    )
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch.object(mod, "SUPABASE_DB_URL", "postgresql://example"), patch.object(
        mod, "_ensure_access_schema", return_value=True
    ), patch.object(mod, "_get_db_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = conn
        result = mod.set_subscription_active("u1", True)

    assert result.subscription_active is True
    assert result.chat_allowed is True
