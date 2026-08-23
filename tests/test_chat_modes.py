"""Chat mode gating: daily practice vs lesson vs companion."""

from unittest.mock import MagicMock, patch

import pytest

from app.chat_service import prepare_chat_context, produce_reply
from app.models import ChatRequest
from app.user_profile import UserProfile


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


_COMPLETED_ONBOARDING = {
    "path_stage": "finding_my_way",
    "primary_reason": "searching_deeper",
    "prior_experience": ["yoga_breathwork"],
    "language_preference": "plain",
    "desired_value": "someone_to_talk",
    "practice_time": "morning",
    "completed_at": "2026-08-22T00:00:00+00:00",
    "version": "cfb_june_2026",
}


@patch("app.chat_service.generate_reply", return_value="ok")
@patch("app.chat_service.load_memory_prompt_block", return_value=None)
@patch("app.chat_service.get_user_profile")
@patch("app.chat_service.check_quota", return_value=True)
def test_companion_completed_onboarding_injected_into_system_prompt(
    _quota,
    mock_profile,
    _memory,
    mock_reply,
):
    mock_profile.return_value = UserProfile(
        user_id="user-1",
        ben_onboarding=_COMPLETED_ONBOARDING,
    )
    req = ChatRequest(session_id="s1", message="Are you Ben?")
    ctx = prepare_chat_context(req, "user-1", _mock_store(), default_provider="openai")
    produce_reply(ctx, MagicMock())

    system_prompt = mock_reply.call_args.kwargs["system_prompt"]
    assert system_prompt is not None
    assert "[SAFETY]" in system_prompt
    assert "not Ben" in system_prompt
    assert "[BEN ONBOARDING]" in system_prompt
    assert "finding_my_way" in system_prompt
    assert "searching_deeper" in system_prompt
    assert "calm meditation assistant" not in system_prompt


@patch("app.chat_service.generate_reply", return_value="ok")
@patch("app.chat_service.load_memory_prompt_block", return_value=None)
@patch("app.chat_service.get_user_profile")
@patch("app.chat_service.check_quota", return_value=True)
def test_companion_incomplete_onboarding_skips_profile_block(
    _quota,
    mock_profile,
    _memory,
    mock_reply,
):
    mock_profile.return_value = UserProfile(
        user_id="user-1",
        ben_onboarding={"path_stage": "just_starting"},
    )
    req = ChatRequest(session_id="s1", message="Hello")
    ctx = prepare_chat_context(req, "user-1", _mock_store(), default_provider="openai")
    produce_reply(ctx, MagicMock())

    system_prompt = mock_reply.call_args.kwargs["system_prompt"]
    assert "[SAFETY]" in system_prompt
    assert "emergency" in system_prompt.lower()
    assert "[BEN ONBOARDING]" not in system_prompt
    assert "[BEN PERSONALISATION]" not in system_prompt


@patch("app.chat_service.generate_reply", return_value="ok")
@patch("app.chat_service.check_entitlement", return_value=True)
@patch("app.chat_service.check_quota", return_value=True)
def test_lesson_chat_does_not_use_companion_system_prompt(
    _quota,
    _entitlement,
    mock_reply,
):
    req = ChatRequest(
        session_id="s1",
        message="What is the witness?",
        course_slug="week-zero-reset",
        week_number=1,
    )
    ctx = prepare_chat_context(req, "user-1", _mock_store(), default_provider="openai")
    produce_reply(ctx, MagicMock())
    assert mock_reply.call_args.kwargs["system_prompt"] is None
