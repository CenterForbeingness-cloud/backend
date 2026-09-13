"""Hardening Phase 2: AI failures must not return fake success replies."""

from unittest.mock import MagicMock, patch

import pytest

from app.ai import (
    AI_UNAVAILABLE_MESSAGE,
    AIUnavailableError,
    generate_reply,
    generate_reply_stream,
)


def test_generate_reply_missing_openai_key_raises() -> None:
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        with pytest.raises(AIUnavailableError, match=AI_UNAVAILABLE_MESSAGE):
            generate_reply("hello", [], "openai")


def test_generate_reply_missing_anthropic_key_raises() -> None:
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
        with pytest.raises(AIUnavailableError, match=AI_UNAVAILABLE_MESSAGE):
            generate_reply("hello", [], "claude")


def test_generate_reply_openai_exception_raises_unavailable() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("quota")

    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False), patch(
        "app.ai._get_openai_client", return_value=client
    ):
        with pytest.raises(AIUnavailableError, match=AI_UNAVAILABLE_MESSAGE):
            generate_reply("hello", [{"role": "user", "content": "hello"}], "openai")


def test_generate_reply_does_not_return_mvp_fallback_text() -> None:
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        with pytest.raises(AIUnavailableError):
            reply = generate_reply("hello", [], "openai")
            assert "[MVP fallback]" not in str(reply)


def test_generate_reply_stream_missing_key_raises() -> None:
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        with pytest.raises(AIUnavailableError, match=AI_UNAVAILABLE_MESSAGE):
            list(generate_reply_stream("hello", [], "openai"))


def test_generate_reply_stream_openai_exception_raises() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("boom")

    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False), patch(
        "app.ai._get_openai_client", return_value=client
    ):
        with pytest.raises(AIUnavailableError, match=AI_UNAVAILABLE_MESSAGE):
            list(
                generate_reply_stream(
                    "hello", [{"role": "user", "content": "hello"}], "openai"
                )
            )
