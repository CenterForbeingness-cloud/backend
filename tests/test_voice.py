import pytest
from fastapi import HTTPException

from app import voice


def test_assert_voice_disabled(monkeypatch):
    monkeypatch.setattr(voice, "VOICE_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        voice.assert_voice_enabled()
    assert exc.value.status_code == 503
    assert exc.value.detail == {"error": "voice_disabled"}


def test_resolve_voice_id_uses_default(monkeypatch):
    monkeypatch.setattr(voice, "SUPABASE_DB_URL", "")
    monkeypatch.setattr(voice, "ELEVENLABS_VOICE_ID_DEFAULT", "voice-default-123")
    assert voice.resolve_voice_id("week-zero-reset") == "voice-default-123"


def test_resolve_voice_id_missing_config(monkeypatch):
    monkeypatch.setattr(voice, "SUPABASE_DB_URL", "")
    monkeypatch.setattr(voice, "ELEVENLABS_VOICE_ID_DEFAULT", "")
    with pytest.raises(HTTPException) as exc:
        voice.resolve_voice_id(None)
    assert exc.value.status_code == 503


def test_check_voice_quota_skips_when_cap_zero(monkeypatch):
    monkeypatch.setattr(voice, "VOICE_DAILY_SECONDS_CAP", 0)
    voice.check_voice_quota("00000000-0000-0000-0000-000000000001", 120.0)
