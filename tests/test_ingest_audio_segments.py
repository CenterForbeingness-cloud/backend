import sys
from pathlib import Path

RAG_SCRIPTS = Path(__file__).resolve().parents[2] / "rag" / "scripts"
sys.path.insert(0, str(RAG_SCRIPTS))

from ingest_audio import segment_chunks  # noqa: E402


def test_segment_chunks_groups_by_duration():
    transcript = {
        "text": "hello world again",
        "segments": [
            {"text": "hello", "start": 0.0, "end": 30.0},
            {"text": "world", "start": 30.0, "end": 60.0},
            {"text": "again", "start": 60.0, "end": 95.0},
        ],
    }
    chunks = segment_chunks(transcript)
    assert len(chunks) == 1
    assert "hello" in chunks[0][0]
    assert chunks[0][1] == 0.0
    assert chunks[0][2] == 95.0


def test_segment_chunks_fallback_without_segments(monkeypatch):
    monkeypatch.setattr(
        "ingest_audio.chunk_text",
        lambda text: [text],
    )
    transcript = {"text": "short lesson text for fallback chunking"}
    chunks = segment_chunks(transcript)
    assert len(chunks) == 1
    assert "short lesson" in chunks[0][0]
