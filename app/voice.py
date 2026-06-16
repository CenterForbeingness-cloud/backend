"""
voice.py — STT (Whisper) and TTS (ElevenLabs) for POST /chat/voice.

Voice is transport only; canonical chat history remains text in chat_messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import HTTPException

from app.config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_MODEL_ID,
    ELEVENLABS_VOICE_ID_DEFAULT,
    OPENAI_API_KEY,
    SUPABASE_DB_URL,
    VOICE_DAILY_SECONDS_CAP,
    VOICE_ENABLED,
    VOICE_MAX_RECORDING_SEC,
    WHISPER_MODEL,
    logger,
)

_voice_schema_bootstrapped = False

_WHISPER_EXTENSIONS = frozenset(
    {"flac", "m4a", "mp3", "mp4", "mpeg", "mpga", "oga", "ogg", "wav", "webm"}
)


def _resolve_whisper_file_format(
    mime_type: str,
    filename: Optional[str] = None,
) -> tuple[str, str]:
    """Return (extension, mime) suitable for OpenAI Whisper from upload metadata."""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext == "mp4":
            return "m4a", "audio/m4a"
        if ext in _WHISPER_EXTENSIONS:
            return ext, f"audio/{ext}" if ext not in {"mp3", "mpeg", "mpga"} else "audio/mpeg"

    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime in {"audio/m4a", "audio/x-m4a", "audio/mp4"}:
        return "m4a", "audio/m4a"
    if "m4a" in mime or mime == "audio/mp4":
        return "m4a", "audio/m4a"
    if "webm" in mime:
        return "webm", mime or "audio/webm"
    if "wav" in mime:
        return "wav", mime or "audio/wav"
    if "mpeg" in mime or "mp3" in mime:
        return "mp3", mime or "audio/mpeg"
    if "ogg" in mime or "oga" in mime:
        return "ogg", mime or "audio/ogg"
    if "flac" in mime:
        return "flac", mime or "audio/flac"

    return "webm", mime or "audio/webm"


def assert_voice_enabled() -> None:
    if not VOICE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail={"error": "voice_disabled"},
        )


def transcribe_audio(
    audio_bytes: bytes,
    *,
    mime_type: str = "audio/webm",
    filename: Optional[str] = None,
) -> Tuple[str, float]:
    """Return (transcript, spoken_seconds_estimate)."""
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Speech transcription is not configured")
    if len(audio_bytes) < 32:
        raise HTTPException(status_code=400, detail="Audio recording too short")

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    ext, whisper_mime = _resolve_whisper_file_format(mime_type, filename)

    try:
        result = client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=(f"utterance.{ext}", audio_bytes, whisper_mime),
            response_format="verbose_json",
        )
    except Exception as exc:
        logger.exception("Whisper transcription failed: %s", exc)
        raise HTTPException(status_code=502, detail="Speech transcription failed") from exc

    text = (getattr(result, "text", None) or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Could not transcribe audio")

    duration = getattr(result, "duration", None)
    spoken_seconds = float(duration) if duration is not None else min(
        VOICE_MAX_RECORDING_SEC, max(len(text.split()) * 0.4, 1.0)
    )
    if spoken_seconds > VOICE_MAX_RECORDING_SEC:
        raise HTTPException(
            status_code=400,
            detail=f"Recording too long (max {VOICE_MAX_RECORDING_SEC}s)",
        )
    return text, spoken_seconds


def _lookup_course_voice_id(course_slug: str) -> Optional[str]:
    if not SUPABASE_DB_URL:
        return None
    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT voice_id FROM public.course_voice_profiles
                WHERE course_slug = %s
                """,
                (course_slug,),
            )
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0])
    except Exception as exc:
        logger.warning("course_voice_profiles lookup failed for %s: %s", course_slug, exc)
    return None


def _lookup_any_course_voice_id() -> Optional[str]:
    if not SUPABASE_DB_URL:
        return None
    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT voice_id FROM public.course_voice_profiles
                WHERE voice_id IS NOT NULL AND voice_id <> ''
                ORDER BY course_slug
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0])
    except Exception as exc:
        logger.warning("course_voice_profiles fallback lookup failed: %s", exc)
    return None


def resolve_voice_id(course_slug: Optional[str]) -> str:
    candidates: list[str] = []
    if course_slug:
        candidates.append(course_slug)
    candidates.append("week-zero-reset")

    seen: set[str] = set()
    for slug in candidates:
        if slug in seen:
            continue
        seen.add(slug)
        voice_id = _lookup_course_voice_id(slug)
        if voice_id:
            return voice_id

    voice_id = _lookup_any_course_voice_id()
    if voice_id:
        return voice_id

    if ELEVENLABS_VOICE_ID_DEFAULT:
        return ELEVENLABS_VOICE_ID_DEFAULT
    raise HTTPException(
        status_code=503,
        detail=(
            "Text-to-speech voice is not configured. "
            "Set ELEVENLABS_VOICE_ID_DEFAULT or add course_voice_profiles."
        ),
    )


def synthesize_speech(text: str, *, course_slug: Optional[str]) -> Tuple[bytes, str]:
    """
    Return (mp3_bytes, mime_type).

    TODO(post-MVP): upload TTS bytes to object storage; return signed URL instead of base64 in API.
    """
    if not ELEVENLABS_API_KEY:
        raise HTTPException(status_code=503, detail="Text-to-speech is not configured")

    voice_id = resolve_voice_id(course_slug)
    import httpx

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text[:5000],
        "model_id": ELEVENLABS_MODEL_ID,
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.content, "audio/mpeg"
    except Exception as exc:
        logger.exception("ElevenLabs TTS failed: %s", exc)
        raise HTTPException(status_code=502, detail="Text-to-speech failed") from exc


def _ensure_voice_usage_schema() -> bool:
    global _voice_schema_bootstrapped
    if _voice_schema_bootstrapped or not SUPABASE_DB_URL:
        return _voice_schema_bootstrapped
    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.user_voice_usage (
                    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
                    voice_seconds_today NUMERIC NOT NULL DEFAULT 0,
                    period_start TIMESTAMPTZ NOT NULL,
                    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        _voice_schema_bootstrapped = True
        return True
    except Exception as exc:
        logger.error("voice usage schema bootstrap failed: %s", exc)
        return False


def check_voice_quota(user_id: str, additional_seconds: float) -> None:
    if VOICE_DAILY_SECONDS_CAP <= 0:
        return
    if not SUPABASE_DB_URL or not _ensure_voice_usage_schema():
        return

    now = datetime.now(timezone.utc)
    period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    from app.db import db_connection

    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT voice_seconds_today, period_start
            FROM public.user_voice_usage
            WHERE user_id = %s::uuid
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            used = float(row[0])
            row_start = row[1]
            if row_start and row_start.date() < period_start.date():
                used = 0.0
        else:
            used = 0.0

        if used + additional_seconds > VOICE_DAILY_SECONDS_CAP:
            raise HTTPException(
                status_code=429,
                detail=f"Daily voice limit reached ({VOICE_DAILY_SECONDS_CAP}s per day)",
            )


def record_voice_usage(user_id: str, spoken_seconds: float) -> None:
    if not SUPABASE_DB_URL or not _ensure_voice_usage_schema():
        return

    now = datetime.now(timezone.utc)
    period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    from app.db import db_connection

    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.user_voice_usage (user_id, voice_seconds_today, period_start)
            VALUES (%s::uuid, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                voice_seconds_today = CASE
                    WHEN user_voice_usage.period_start::date < EXCLUDED.period_start::date
                    THEN EXCLUDED.voice_seconds_today
                    ELSE user_voice_usage.voice_seconds_today + EXCLUDED.voice_seconds_today
                END,
                period_start = EXCLUDED.period_start,
                last_updated_at = NOW()
            """,
            (user_id, spoken_seconds, period_start),
        )
