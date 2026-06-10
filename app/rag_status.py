"""
rag_status.py — Read-only corpus / voice status for admin and health gates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.config import SUPABASE_DB_URL, logger

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAG_ROOT = _REPO_ROOT / "rag"
_RAW_COURSES = _RAG_ROOT / "raw" / "courses"
_MANIFESTS = _RAG_ROOT / "processed" / "manifests"
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".webm", ".ogg"}
_TEXT_EXTENSIONS = {".txt", ".md"}


@dataclass
class CourseRagStatus:
    course_slug: str
    text_files: int = 0
    audio_files: int = 0
    last_text_manifest_at: Optional[str] = None
    last_audio_manifest_at: Optional[str] = None
    text_chunk_count: Optional[int] = None
    audio_chunk_count: Optional[int] = None
    voice_id: Optional[str] = None
    voice_provider: Optional[str] = None
    voice_configured: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_slug": self.course_slug,
            "text_files": self.text_files,
            "audio_files": self.audio_files,
            "last_text_manifest_at": self.last_text_manifest_at,
            "last_audio_manifest_at": self.last_audio_manifest_at,
            "text_chunk_count": self.text_chunk_count,
            "audio_chunk_count": self.audio_chunk_count,
            "voice_configured": self.voice_configured,
            "voice_provider": self.voice_provider,
        }


def _count_files(root: Path, extensions: set[str]) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file() and p.suffix.lower() in extensions)


def _latest_manifest(prefix: str, course_slug: str) -> Optional[dict]:
    if not _MANIFESTS.is_dir():
        return None
    matches = sorted(
        _MANIFESTS.glob(f"{prefix}*{course_slug}*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not matches and prefix == "audio_":
        matches = sorted(
            _MANIFESTS.glob(f"{prefix}*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    for path in matches:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("course_slug") == course_slug or prefix != "audio_":
                if prefix == "audio_" and data.get("course_slug") != course_slug:
                    continue
                data["_manifest_path"] = str(path.name)
                return data
        except Exception as exc:
            logger.warning("Could not read manifest %s: %s", path, exc)
    return None


def _load_voice_profile(course_slug: str) -> tuple[Optional[str], Optional[str], bool]:
    if not SUPABASE_DB_URL:
        return None, None, False
    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT voice_id, provider
                FROM public.course_voice_profiles
                WHERE course_slug = %s
                """,
                (course_slug,),
            )
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0]), str(row[1] or "elevenlabs"), True
    except Exception as exc:
        logger.warning("course_voice_profiles read failed: %s", exc)
    return None, None, False


def get_course_rag_status(course_slug: str) -> CourseRagStatus:
    course_raw = _RAW_COURSES / course_slug
    text_files = _count_files(course_raw, _TEXT_EXTENSIONS)
    audio_dir = course_raw / "audio"
    audio_files = _count_files(audio_dir, _AUDIO_EXTENSIONS) if audio_dir.is_dir() else 0

    text_manifest = _latest_manifest("manifest_", course_slug)
    audio_manifest = _latest_manifest("audio_", course_slug)

    voice_id, voice_provider, voice_ok = _load_voice_profile(course_slug)

    return CourseRagStatus(
        course_slug=course_slug,
        text_files=text_files,
        audio_files=audio_files,
        last_text_manifest_at=(
            text_manifest.get("generated_at") if text_manifest else None
        ),
        last_audio_manifest_at=(
            audio_manifest.get("generated_at") if audio_manifest else None
        ),
        text_chunk_count=(
            int(text_manifest["total_chunks"])
            if text_manifest and text_manifest.get("total_chunks") is not None
            else None
        ),
        audio_chunk_count=(
            int(audio_manifest["total_chunks"])
            if audio_manifest and audio_manifest.get("total_chunks") is not None
            else None
        ),
        voice_id=voice_id,
        voice_provider=voice_provider,
        voice_configured=voice_ok,
    )


def summarize_voice_readiness(course_slug: Optional[str] = None) -> CourseRagStatus:
    slug = course_slug or "week-zero-reset"
    return get_course_rag_status(slug)
