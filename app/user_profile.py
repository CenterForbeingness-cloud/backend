"""
user_profile.py — Phase 1 thin memory plus Ben onboarding JSON on the same row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import SUPABASE_DB_URL, logger

_schema_bootstrapped = False

BEN_ONBOARDING_VERSION = "cfb_june_2026"

BEN_ONBOARDING_OPTION_KEYS: dict[str, frozenset[str]] = {
    "path_stage": frozenset(
        {"just_starting", "finding_my_way", "been_on_path", "deep_in_it"}
    ),
    "primary_reason": frozenset(
        {
            "peace_less_stress",
            "searching_deeper",
            "had_glimpse",
            "daily_practice",
            "something_shifting",
        }
    ),
    "prior_experience": frozenset(
        {
            "mindfulness_apps",
            "yoga_breathwork",
            "retreats_immersions",
            "teachers_satsang",
            "plant_medicine",
            "therapy_psychology",
            "starting_fresh",
        }
    ),
    "language_preference": frozenset(
        {"plain", "mindfulness", "nondual", "tradition", "open"}
    ),
    "desired_value": frozenset(
        {
            "guided_meditations",
            "someone_to_talk",
            "understand_awareness",
            "support_difficulty",
            "all_of_it",
        }
    ),
    "practice_time": frozenset(
        {"morning", "during_day", "evening", "late_night", "whenever"}
    ),
}

BEN_ONBOARDING_REQUIRED_FIELDS = (
    "path_stage",
    "primary_reason",
    "prior_experience",
    "language_preference",
    "desired_value",
    "practice_time",
)

PRIMARY_REASON_LABELS: dict[str, str] = {
    "peace_less_stress": "More peace, less stress",
    "searching_deeper": "Searching for something deeper",
    "had_glimpse": "A glimpse or opening to understand",
    "daily_practice": "A daily practice I can maintain",
    "something_shifting": "Something is shifting; I need support",
}

DESIRED_VALUE_LABELS: dict[str, str] = {
    "guided_meditations": "Daily guided meditations",
    "someone_to_talk": "Someone to talk with about experience",
    "understand_awareness": "Help understanding what awareness is",
    "support_difficulty": "Support through a difficult time",
    "all_of_it": "Full practice",
}

_ONBOARDING_PATCH_FIELDS = frozenset(
    {
        "path_stage",
        "primary_reason",
        "prior_experience",
        "language_preference",
        "desired_value",
        "practice_time",
        "tradition_detail",
    }
)


class BenOnboardingValidationError(ValueError):
    """Client sent invalid Ben onboarding keys or an incomplete complete-request."""


@dataclass
class UserProfile:
    user_id: str
    display_name: Optional[str] = None
    primary_goal: Optional[str] = None
    secondary_goal: Optional[str] = None
    current_focus: Optional[str] = None
    energy_level: Optional[str] = None
    motivation_type: Optional[str] = None
    ben_onboarding: Optional[dict[str, Any]] = None
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
                    ben_onboarding JSONB,
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
            cur.execute(
                """
                ALTER TABLE public.user_profile
                ADD COLUMN IF NOT EXISTS ben_onboarding JSONB
                """
            )
        _schema_bootstrapped = True
        return True
    except Exception as exc:
        logger.error("Failed to bootstrap user_profile schema: %s", exc)
        return False


def _parse_onboarding(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    return value


def _serialize_onboarding(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    completed = out.get("completed_at")
    if isinstance(completed, datetime):
        out["completed_at"] = completed.astimezone(timezone.utc).isoformat()
    return out


def _onboarding_sql_value(data: Optional[dict[str, Any]]) -> Optional[str]:
    if data is None:
        return None
    return json.dumps(_serialize_onboarding(data))


def normalize_prior_experience(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise BenOnboardingValidationError("prior_experience must be a list of keys")
    keys: list[str] = []
    allowed = BEN_ONBOARDING_OPTION_KEYS["prior_experience"]
    for item in values:
        if not isinstance(item, str) or item not in allowed:
            raise BenOnboardingValidationError(f"Invalid prior_experience: {item}")
        if item not in keys:
            keys.append(item)
    if "starting_fresh" in keys:
        return ["starting_fresh"]
    return keys


def validate_ben_onboarding_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Keep known fields, reject invalid option keys. None values are omitted."""
    cleaned: dict[str, Any] = {}
    for field, value in patch.items():
        if field not in _ONBOARDING_PATCH_FIELDS:
            continue
        if value is None:
            continue
        if field == "prior_experience":
            cleaned[field] = normalize_prior_experience(value)
            continue
        if field == "tradition_detail":
            if isinstance(value, str):
                text = value.strip()
                cleaned[field] = text or None
            else:
                raise BenOnboardingValidationError("tradition_detail must be text")
            continue
        if not isinstance(value, str) or value not in BEN_ONBOARDING_OPTION_KEYS[field]:
            raise BenOnboardingValidationError(f"Invalid {field}: {value}")
        cleaned[field] = value
    if (
        cleaned.get("language_preference")
        and cleaned["language_preference"] != "tradition"
    ):
        cleaned["tradition_detail"] = None
    return cleaned


def merge_ben_onboarding(
    existing: Optional[dict[str, Any]],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Partial save: merge answers without wiping other keys or completed_at."""
    incoming = validate_ben_onboarding_patch(patch)
    merged = dict(existing or {})
    merged.update(incoming)
    return merged


def missing_ben_onboarding_fields(data: Optional[dict[str, Any]]) -> list[str]:
    payload = data or {}
    missing: list[str] = []
    for field in BEN_ONBOARDING_REQUIRED_FIELDS:
        value = payload.get(field)
        if field == "prior_experience":
            if not value:
                missing.append(field)
            continue
        if not isinstance(value, str) or not value.strip():
            missing.append(field)
    return missing


def validate_ben_onboarding_complete(data: Optional[dict[str, Any]]) -> dict[str, Any]:
    payload = dict(data or {})
    missing = missing_ben_onboarding_fields(payload)
    if missing:
        raise BenOnboardingValidationError(
            "Missing onboarding answers: " + ", ".join(missing)
        )
    payload["prior_experience"] = normalize_prior_experience(payload["prior_experience"])
    for field in BEN_ONBOARDING_REQUIRED_FIELDS:
        if field == "prior_experience":
            continue
        if payload[field] not in BEN_ONBOARDING_OPTION_KEYS[field]:
            raise BenOnboardingValidationError(f"Invalid {field}: {payload[field]}")
    if payload.get("language_preference") != "tradition":
        payload["tradition_detail"] = None
    elif isinstance(payload.get("tradition_detail"), str):
        payload["tradition_detail"] = payload["tradition_detail"].strip() or None
    return payload


def profile_ben_onboarding_complete(profile: Optional[UserProfile]) -> bool:
    if profile is None or not profile.ben_onboarding:
        return False
    completed = profile.ben_onboarding.get("completed_at")
    return bool(completed)


def _row_to_profile(row: tuple) -> UserProfile:
    return UserProfile(
        user_id=str(row[0]),
        display_name=row[1],
        primary_goal=row[2],
        secondary_goal=row[3],
        current_focus=row[4],
        energy_level=row[5],
        motivation_type=row[6],
        ben_onboarding=_parse_onboarding(row[7]),
        created_at=row[8],
        updated_at=row[9],
    )


_PROFILE_COLUMNS = """
    user_id, display_name, primary_goal, secondary_goal,
    current_focus, energy_level, motivation_type, ben_onboarding,
    created_at, updated_at
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


def upsert_user_profile(
    user_id: str,
    fields: dict[str, Any],
    *,
    merge_onboarding: bool = True,
) -> UserProfile:
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
    updates: dict[str, Any] = {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in fields.items()
        if k in allowed and v is not None
    }
    for k, v in list(updates.items()):
        if isinstance(v, str) and not v:
            updates[k] = None

    now = datetime.now(timezone.utc)
    existing = get_user_profile(user_id)

    if "ben_onboarding" in fields and fields["ben_onboarding"] is not None:
        incoming = fields["ben_onboarding"]
        if not isinstance(incoming, dict):
            raise BenOnboardingValidationError("ben_onboarding must be an object")
        if merge_onboarding:
            updates["ben_onboarding"] = merge_ben_onboarding(
                existing.ben_onboarding if existing else None,
                incoming,
            )
        else:
            updates["ben_onboarding"] = incoming

    try:
        with _get_db_connection() as conn, conn.cursor() as cur:
            onboarding_value = _onboarding_sql_value(
                updates["ben_onboarding"]
                if "ben_onboarding" in updates
                else (existing.ben_onboarding if existing else None)
            )
            if existing is None:
                cur.execute(
                    f"""
                    INSERT INTO public.user_profile ({_PROFILE_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
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
                        onboarding_value,
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
                    "ben_onboarding": existing.ben_onboarding,
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
                        ben_onboarding = %s::jsonb,
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
                        _onboarding_sql_value(merged["ben_onboarding"]),
                        now,
                        user_id,
                    ),
                )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("upsert_user_profile returned no row")
            return _row_to_profile(row)
    except BenOnboardingValidationError:
        raise
    except Exception as exc:
        logger.error("upsert_user_profile failed for %s: %s", user_id, exc)
        raise


def complete_ben_onboarding(user_id: str) -> UserProfile:
    """Validate all six answers, set completed_at, optionally prefill Phase 1 fields."""
    existing = get_user_profile(user_id)
    payload = validate_ben_onboarding_complete(
        existing.ben_onboarding if existing else None
    )
    now = datetime.now(timezone.utc)
    payload["completed_at"] = now.isoformat()
    payload["version"] = BEN_ONBOARDING_VERSION

    fields: dict[str, Any] = {"ben_onboarding": payload}
    if existing is None or not (existing.primary_goal or "").strip():
        fields["primary_goal"] = PRIMARY_REASON_LABELS[payload["primary_reason"]]
    if existing is None or not (existing.current_focus or "").strip():
        fields["current_focus"] = DESIRED_VALUE_LABELS[payload["desired_value"]]

    return upsert_user_profile(user_id, fields, merge_onboarding=False)


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
