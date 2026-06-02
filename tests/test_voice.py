import pytest
from fastapi import HTTPException

from app import voice


def test_assert_voice_disabled(monkeypatch):
    monkeypatch.setattr(voice, "VOICE_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        voice.assert_voice_enabled()
    assert exc.value.status_code == 503
    assert exc.value.detail == {"error": "voice_disabled"}
