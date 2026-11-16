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


def test_resolve_voice_id_companion_prefers_ben_default(monkeypatch):
    monkeypatch.setattr(voice, "SUPABASE_DB_URL", "postgresql://example")
    monkeypatch.setattr(voice, "ELEVENLABS_VOICE_ID_DEFAULT", "JF4dNU1bkDUz9kbCiEER")
    monkeypatch.setattr(voice, "_lookup_course_voice_id", lambda slug: "voice-old")
    monkeypatch.setattr(voice, "_lookup_any_course_voice_id", lambda: "voice-any")
    assert voice.resolve_voice_id(None) == "JF4dNU1bkDUz9kbCiEER"


def test_resolve_voice_id_falls_back_to_any_course(monkeypatch):
    monkeypatch.setattr(voice, "SUPABASE_DB_URL", "postgresql://example")
    monkeypatch.setattr(voice, "ELEVENLABS_VOICE_ID_DEFAULT", "")
    monkeypatch.setattr(voice, "_lookup_course_voice_id", lambda slug: None)
    monkeypatch.setattr(voice, "_lookup_any_course_voice_id", lambda: "voice-any")
    assert voice.resolve_voice_id(None) == "voice-any"


def test_resolve_voice_id_prefers_requested_course(monkeypatch):
    monkeypatch.setattr(voice, "SUPABASE_DB_URL", "postgresql://example")
    monkeypatch.setattr(voice, "ELEVENLABS_VOICE_ID_DEFAULT", "JF4dNU1bkDUz9kbCiEER")

    def fake_lookup(slug: str):
        return {"deep-calm": "voice-dc"}.get(slug)

    monkeypatch.setattr(voice, "_lookup_course_voice_id", fake_lookup)
    monkeypatch.setattr(voice, "_lookup_any_course_voice_id", lambda: None)
    assert voice.resolve_voice_id("deep-calm") == "voice-dc"
    assert voice.resolve_voice_id("unknown-course") == "JF4dNU1bkDUz9kbCiEER"


def test_resolve_voice_id_missing_config(monkeypatch):
    monkeypatch.setattr(voice, "SUPABASE_DB_URL", "")
    monkeypatch.setattr(voice, "ELEVENLABS_VOICE_ID_DEFAULT", "")
    with pytest.raises(HTTPException) as exc:
        voice.resolve_voice_id(None)
    assert exc.value.status_code == 503


def test_check_voice_quota_skips_when_cap_zero(monkeypatch):
    monkeypatch.setattr(voice, "VOICE_DAILY_SECONDS_CAP", 0)
    voice.check_voice_quota("00000000-0000-0000-0000-000000000001", 120.0)


@pytest.mark.parametrize(
    ("mime_type", "filename", "expected_ext"),
    [
        ("audio/mp4", "utterance.m4a", "m4a"),
        ("audio/mp4", None, "m4a"),
        ("audio/m4a", None, "m4a"),
        ("audio/webm", "utterance.webm", "webm"),
        ("audio/wav", None, "wav"),
        ("audio/mpeg", None, "mp3"),
    ],
)
def test_resolve_whisper_file_format(mime_type, filename, expected_ext):
    ext, _ = voice._resolve_whisper_file_format(mime_type, filename)
    assert ext == expected_ext
