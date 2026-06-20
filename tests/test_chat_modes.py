"""Chat mode gating: daily practice vs lesson vs companion."""

from unittest.mock import MagicMock, patch

import pytest

from app.chat_service import prepare_chat_context
from app.models import ChatRequest


def _mock_store():
    store = MagicMock()
    store.get_history.return_value = []
    return store


@patch("app.chat_service.check_entitlement", return_value=True)
@patch("app.chat_service.check_quota", return_value=True)
@patch("app.chat_service.get_schedule_day")
@patch("app.chat_service.resolve_schedule_day_number")
def test_lesson_chat_skips_schedule(
    mock_resolve_day,
    mock_get_day,
    _quota,
    _entitlement,
):
    mock_resolve_day.return_value = 1
    mock_get_day.return_value = {
        "day_number": 1,
        "day_title": "Test",
        "content": "themes",
    }

    req = ChatRequest(
        session_id="s1",
        message="What is the witness?",
        course_slug="week-zero-reset",
        week_number=1,
    )
    ctx = prepare_chat_context(req, "user-1", _mock_store(), default_provider="openai")

    mock_resolve_day.assert_not_called()
    assert ctx.schedule_system_block is None
    assert ctx.schedule_day_number is None


@patch("app.chat_service.check_entitlement", return_value=True)
@patch("app.chat_service.check_quota", return_value=True)
@patch("app.chat_service.build_schedule_context_block", return_value="[GUIDE BLOCK]")
@patch("app.chat_service.get_schedule_day")
@patch("app.chat_service.resolve_schedule_day_number", return_value=2)
def test_daily_practice_loads_schedule(
    mock_resolve_day,
    mock_get_day,
    mock_build_block,
    _quota,
    _entitlement,
):
    mock_get_day.return_value = {
        "day_number": 2,
        "day_title": "Body",
        "content": "themes",
    }

    req = ChatRequest(
        session_id="s1",
        message="Hello",
        course_slug="week-zero-reset",
        daily_practice=True,
    )
    ctx = prepare_chat_context(req, "user-1", _mock_store(), default_provider="openai")

    mock_resolve_day.assert_called_once()
    assert ctx.schedule_day_number == 2
    assert ctx.schedule_system_block == "[GUIDE BLOCK]"
    mock_build_block.assert_called_once()


@patch("app.chat_service.check_entitlement", return_value=True)
@patch("app.chat_service.check_quota", return_value=True)
@patch("app.chat_service.resolve_schedule_day_number")
def test_course_slug_without_daily_practice_no_schedule(
    mock_resolve_day,
    _quota,
    _entitlement,
):
    req = ChatRequest(
        session_id="s1",
        message="Hello",
        course_slug="week-zero-reset",
    )
    ctx = prepare_chat_context(req, "user-1", _mock_store(), default_provider="openai")

    mock_resolve_day.assert_not_called()
    assert ctx.schedule_system_block is None
