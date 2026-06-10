from pathlib import Path
from unittest.mock import MagicMock, patch

from app.rag_status import get_course_rag_status, summarize_voice_readiness
from app.voice_gates import evaluate_voice_gates


def test_get_course_rag_status_empty_course(tmp_path, monkeypatch):
    monkeypatch.setattr("app.rag_status._RAG_ROOT", tmp_path / "rag")
    monkeypatch.setattr("app.rag_status._RAW_COURSES", tmp_path / "rag" / "raw" / "courses")
    monkeypatch.setattr("app.rag_status._MANIFESTS", tmp_path / "rag" / "processed" / "manifests")

    status = get_course_rag_status("week-zero-reset")
    assert status.course_slug == "week-zero-reset"
    assert status.text_files == 0
    assert status.audio_files == 0
    assert status.voice_configured is False


def test_get_course_rag_status_counts_audio(tmp_path, monkeypatch):
    rag_root = tmp_path / "rag"
    course_dir = rag_root / "raw" / "courses" / "week-zero-reset" / "audio"
    course_dir.mkdir(parents=True)
    (course_dir / "lesson-01.mp3").write_bytes(b"x")

    monkeypatch.setattr("app.rag_status._RAG_ROOT", rag_root)
    monkeypatch.setattr("app.rag_status._RAW_COURSES", rag_root / "raw" / "courses")
    monkeypatch.setattr("app.rag_status._MANIFESTS", rag_root / "processed" / "manifests")
    monkeypatch.setattr("app.rag_status.SUPABASE_DB_URL", "")

    status = get_course_rag_status("week-zero-reset")
    assert status.audio_files == 1


def test_voice_gates_structure(monkeypatch):
    monkeypatch.setattr("app.voice_gates.VOICE_ENABLED", False)
    monkeypatch.setattr("app.voice_gates.RAG_ENABLED", False)
    result = evaluate_voice_gates(course_slug="week-zero-reset")
    assert result["track"] == "C"
    assert "checks" in result
    assert "ready" in result
    assert isinstance(result["blocking"], list)


def test_summarize_voice_readiness_default_slug(monkeypatch):
    monkeypatch.setattr(
        "app.rag_status.get_course_rag_status",
        lambda slug: type("S", (), {"course_slug": slug, "audio_files": 0})(),
    )
    status = summarize_voice_readiness()
    assert status.course_slug == "week-zero-reset"
