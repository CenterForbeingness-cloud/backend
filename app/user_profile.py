"""
user_profile.py — Phase 1 thin memory (stable identity + focus for /chat prompts).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import SUPABASE_DB_URL, logger

_schema_bootstrapped = False


@dataclass
class UserProfile:
    user_id: str
    display_name: Optional[str] = None
    primary_goal: Optional[str] = None
    secondary_goal: Optional[str] = None
    current_focus: Optional[str] = None
    energy_level: Optional[str] = None
    motivation_type: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _get_db_connection():
    from app.db import db_connection

    return db_connection()


def _ensure_profile_schema() -> bool:
    global _schema_bootstrapped
    if _schema_bootstrapped or not SUPABASE_DB_URL:
        return _schema_bootstrapped

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.user_profile (
                    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
                    display_name TEXT,
                    primary_goal TEXT,
                    secondary_goal TEXT,
                    current_focus TEXT,
                    energy_level TEXT,
                    motivation_type TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_profile_updated_at
                ON public.user_profile (updated_at DESC)
                """
            )
        _schema_bootstrapped = True
        return True
    except Exception as exc:
        logger.error("Failed to bootstrap user_profile schema: %s", exc)
        return False


def _row_to_profile(row: tuple) -> UserProfile:
    return UserProfile(
        user_id=str(row[0]),
        display_name=row[1],
        primary_goal=row[2],
        secondary_goal=row[3],
        current_focus=row[4],
        energy_level=row[5],
        motivation_type=row[6],
        created_at=row[7],
        updated_at=row[8],
    )


_PROFILE_COLUMNS = """
    user_id, display_name, primary_goal, secondary_goal,
    current_focus, energy_level, motivation_type, created_at, updated_at
"""


def get_user_profile(user_id: str) -> Optional[UserProfile]:
    if not SUPABASE_DB_URL:
        return None
    if not _ensure_profile_schema():
        return None

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_PROFILE_COLUMNS} FROM public.user_profile WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_profile(row)
    except Exception as exc:
        logger.error("get_user_profile failed for %s: %s", user_id, exc)
        return None


def upsert_user_profile(user_id: str, fields: dict[str, Any]) -> UserProfile:
    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")

    if not _ensure_profile_schema():
        raise RuntimeError("user_profile schema unavailable")

    allowed = {
        "display_name",
        "primary_goal",
        "secondary_goal",
        "current_focus",
        "energy_level",
        "motivation_type",
    }
    updates = {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in fields.items()
        if k in allowed and v is not None
    }
    for k, v in list(updates.items()):
        if isinstance(v, str) and not v:
            updates[k] = None

    now = datetime.now(timezone.utc)
    existing = get_user_profile(user_id)

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            if existing is None:
                cur.execute(
                    f"""
                    INSERT INTO public.user_profile ({_PROFILE_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_PROFILE_COLUMNS}
                    """,
                    (
                        user_id,
                        updates.get("display_name"),
                        updates.get("primary_goal"),
                        updates.get("secondary_goal"),
                        updates.get("current_focus"),
                        updates.get("energy_level"),
                        updates.get("motivation_type"),
                        now,
                        now,
                    ),
                )
            else:
                merged = {
                    "display_name": existing.display_name,
                    "primary_goal": existing.primary_goal,
                    "secondary_goal": existing.secondary_goal,
                    "current_focus": existing.current_focus,
                    "energy_level": existing.energy_level,
                    "motivation_type": existing.motivation_type,
                }
                merged.update(updates)
                cur.execute(
                    f"""
                    UPDATE public.user_profile
                    SET display_name = %s,
                        primary_goal = %s,
                        secondary_goal = %s,
                        current_focus = %s,
                        energy_level = %s,
                        motivation_type = %s,
                        updated_at = %s
                    WHERE user_id = %s
                    RETURNING {_PROFILE_COLUMNS}
                    """,
                    (
                        merged["display_name"],
                        merged["primary_goal"],
                        merged["secondary_goal"],
                        merged["current_focus"],
                        merged["energy_level"],
                        merged["motivation_type"],
                        now,
                        user_id,
                    ),
                )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("upsert_user_profile returned no row")
            return _row_to_profile(row)
    except Exception as exc:
        logger.error("upsert_user_profile failed for %s: %s", user_id, exc)
        raise


def profile_has_launch_memory(profile: Optional[UserProfile]) -> bool:
    """True when Phase 1 minimum is set (goal or focus)."""
    if profile is None:
        return False
    goal = (profile.primary_goal or "").strip()
    focus = (profile.current_focus or "").strip()
    return bool(goal or focus)


def format_profile_system_block(profile: Optional[UserProfile]) -> Optional[str]:
    """Compact block injected into every /chat system prompt."""
    if profile is None:
        return None

    lines: list[str] = []
    if profile.display_name:
        lines.append(f"Display name: {profile.display_name.strip()}")
    if profile.primary_goal and profile.primary_goal.strip():
        lines.append(f"Primary goal: {profile.primary_goal.strip()}")
    if profile.secondary_goal and profile.secondary_goal.strip():
        lines.append(f"Secondary goal: {profile.secondary_goal.strip()}")
    if profile.current_focus and profile.current_focus.strip():
        lines.append(f"Current focus: {profile.current_focus.strip()}")
    if profile.energy_level and profile.energy_level.strip():
        lines.append(f"Energy level: {profile.energy_level.strip()}")
    if profile.motivation_type and profile.motivation_type.strip():
        lines.append(f"Motivation type: {profile.motivation_type.strip()}")

    if not lines:
        return None

    body = "\n".join(lines)
    return (
        "[USER PROFILE]\n"
        f"{body}\n\n"
        "You are the user's personal companion. Use this context naturally when relevant. "
        "Do not invent facts beyond what is listed here. "
        "If they ask what you remember, refer only to this profile."
    )


def load_profile_prompt_block(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    return format_profile_system_block(get_user_profile(user_id))
