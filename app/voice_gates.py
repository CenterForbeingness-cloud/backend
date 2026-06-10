"""
voice_gates.py — Track C readiness checks (env + optional corpus hints, no secrets in output).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID_DEFAULT,
    OPENAI_API_KEY,
    PINECONE_API_KEY,
    PINECONE_INDEX_NAME,
    RAG_ENABLED,
    VOICE_ENABLED,
)
from app.rag_status import summarize_voice_readiness


@dataclass(frozen=True)
class VoiceGateCheck:
    gate_id: str
    name: str
    ok: bool
    detail: str
    required: bool = True


def evaluate_voice_gates(*, course_slug: str | None = None) -> dict:
    """
    Evaluate Track C env gates. Returns summary dict safe for /health/voice-gates.
    """
    checks: list[VoiceGateCheck] = []

    checks.append(
        VoiceGateCheck(
            "C6a",
            "VOICE_ENABLED",
            VOICE_ENABLED,
            "true" if VOICE_ENABLED else "set VOICE_ENABLED=true after QA",
        )
    )
    openai_ok = bool((OPENAI_API_KEY or "").strip())
    checks.append(
        VoiceGateCheck(
            "C4a",
            "OPENAI_API_KEY",
            openai_ok,
            "configured" if openai_ok else "required for Whisper STT + embeddings",
            required=VOICE_ENABLED,
        )
    )
    eleven_ok = bool((ELEVENLABS_API_KEY or "").strip())
    checks.append(
        VoiceGateCheck(
            "C4b",
            "ELEVENLABS_API_KEY",
            eleven_ok,
            "configured" if eleven_ok else "required for TTS",
            required=VOICE_ENABLED,
        )
    )
    default_voice_ok = bool((ELEVENLABS_VOICE_ID_DEFAULT or "").strip())
    checks.append(
        VoiceGateCheck(
            "C3a",
            "ELEVENLABS_VOICE_ID_DEFAULT",
            default_voice_ok,
            "configured" if default_voice_ok else "set default or per-course voice_id in DB",
            required=False,
        )
    )
    checks.append(
        VoiceGateCheck(
            "C2a",
            "RAG_ENABLED",
            RAG_ENABLED,
            "true" if RAG_ENABLED else "set RAG_ENABLED=true for course-grounded voice",
            required=False,
        )
    )
    pinecone_ok = bool((PINECONE_API_KEY or "").strip()) and bool(
        (PINECONE_INDEX_NAME or "").strip()
    )
    checks.append(
        VoiceGateCheck(
            "C2b",
            "PINECONE_INDEX",
            pinecone_ok or not RAG_ENABLED,
            "configured" if pinecone_ok else "PINECONE_API_KEY + PINECONE_INDEX_NAME",
            required=RAG_ENABLED,
        )
    )

    corpus = summarize_voice_readiness(course_slug)
    checks.append(
        VoiceGateCheck(
            "C1a",
            "AUDIO_CORPUS_ON_DISK",
            corpus.audio_files > 0,
            f"{corpus.audio_files} file(s) under rag/raw/courses/"
            + (course_slug or "*")
            + "/audio",
            required=False,
        )
    )
    checks.append(
        VoiceGateCheck(
            "C2c",
            "AUDIO_MANIFEST",
            corpus.audio_chunk_count is not None and corpus.audio_chunk_count > 0,
            corpus.last_audio_manifest_at or "run ingest_audio.py after adding audio",
            required=False,
        )
    )
    checks.append(
        VoiceGateCheck(
            "C3b",
            "COURSE_VOICE_PROFILE",
            corpus.voice_configured,
            "voice_id in course_voice_profiles"
            if corpus.voice_configured
            else "admin PATCH /voice or SQL insert",
            required=False,
        )
    )

    required_checks = [c for c in checks if c.required]
    blocking = [f"{c.gate_id} {c.name}" for c in required_checks if not c.ok]
    ready = len(blocking) == 0

    return {
        "ready": ready,
        "track": "C",
        "voice_enabled": VOICE_ENABLED,
        "rag_enabled": RAG_ENABLED,
        "corpus": corpus.to_dict(),
        "blocking": blocking,
        "checks": [
            {
                "gate_id": c.gate_id,
                "name": c.name,
                "ok": c.ok,
                "detail": c.detail,
                "required": c.required,
            }
            for c in checks
        ],
    }
