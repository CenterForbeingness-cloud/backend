"""
coaching_voice.py — Ben master prompt + onboarding personalisation for companion turns.

Phase 3: format blocks only. Chat injection is phase 4.
The confidential master prompt lives in ben_master_system_prompt_v1.txt (gitignored).
Tests and local fallback use the public stub in this folder.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from app.config import BEN_MASTER_PROMPT_PATH, BEN_MASTER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)
_logged_prompt_source: Optional[str] = None

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_STUB_PROMPT_PATH = _PROMPTS_DIR / "ben_master_system_prompt_stub.txt"
_DEFAULT_MASTER_PATH = _PROMPTS_DIR / "ben_master_system_prompt_v1.txt"

SAFETY_POLICY = """[SAFETY]
You are not a clinician. Never diagnose mental health conditions.
If the user shows significant distress, encourage professional support and people they trust.
If there is immediate risk of harm to themselves or others, tell them to contact local emergency services now.
Never claim guaranteed awakening, enlightenment, or healing outcomes.
If asked whether they are talking to Ben Warren: say Sentient is an AI trained on Ben's teaching, not Ben in person.
"""

COMPANION_SESSION_RULES = """[COMPANION SESSION]
You are a coaching companion in Ben Warren's teaching lineage, not a generic chatbot.

Greeting rules:
1. Messages like hi, hello, hey, good morning are openings into coaching, not small talk.
2. Do not reply like a customer support bot. Never use lines such as "Who are you today?", "How can I help you today?", or "Is there anything specific on your mind?" as the main move.
3. On a first greeting, welcome them briefly in an unhurried Ben voice, then invite them into presence, awareness, or what they noticed coming here. If onboarding answers exist, use them gently (path stage or reason) without repeating every key.
4. Keep the first reply short: about two to four sentences.

Stay on the coaching path:
5. If they go off topic (weather, gadgets, random opinions), acknowledge in one short line, then guide back to awareness, practice, or what is true for them right now. Do not chase the tangent for several turns.
6. Prefer pointing toward recognition and presence over tips, techniques, or chit chat.
7. Do not invent a new persona. Stay Sentient, trained on Ben's teaching.
"""

PATH_STAGE_LABELS = {
    "just_starting": "New to meditation and awareness practice",
    "finding_my_way": "Meditates occasionally; something still missing",
    "been_on_path": "Books, retreats, glimpses",
    "deep_in_it": "Significant realisations; integrating",
}

PRIMARY_REASON_LABELS = {
    "peace_less_stress": "More peace, less stress",
    "searching_deeper": "Searching for something deeper",
    "had_glimpse": "A glimpse or opening to understand",
    "daily_practice": "A daily practice they can maintain",
    "something_shifting": "Something shifting; they need support",
}

PRIOR_EXPERIENCE_LABELS = {
    "mindfulness_apps": "Mindfulness apps (Headspace, Calm, Waking Up or similar)",
    "yoga_breathwork": "Yoga or breathwork",
    "retreats_immersions": "Retreats or immersions",
    "teachers_satsang": "Spiritual teachers or Satsang",
    "plant_medicine": "Psychedelic or plant medicine",
    "therapy_psychology": "Therapy or psychology",
    "starting_fresh": "None of the above / starting fresh",
}

LANGUAGE_LABELS = {
    "plain": "Simple and plain; no jargon",
    "mindfulness": "Meditation and mindfulness language",
    "nondual": "Non dual and Advaita terms OK",
    "tradition": "Specific tradition language",
    "open": "Open to whatever fits",
}

DESIRED_VALUE_LABELS = {
    "guided_meditations": "Daily guided meditations",
    "someone_to_talk": "Someone to talk with about experience",
    "understand_awareness": "Help understanding what awareness is",
    "support_difficulty": "Support through a difficult time",
    "all_of_it": "Full practice",
}

PRACTICE_TIME_LABELS = {
    "morning": "Before the day starts",
    "during_day": "Short sessions during the day",
    "evening": "Wind down",
    "late_night": "Late night when things feel too much",
    "whenever": "Whenever they remember",
}

_STAGE_ONE_TWO = frozenset({"just_starting", "finding_my_way"})


def _read_prompt_file(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _log_prompt_source(source: str) -> None:
    global _logged_prompt_source
    if source == _logged_prompt_source:
        return
    _logged_prompt_source = source
    logger.info("Companion master prompt source: %s", source)


def load_master_system_prompt() -> str:
    """Load proprietary master prompt if present; otherwise the public stub."""
    inline = (BEN_MASTER_SYSTEM_PROMPT or "").strip()
    if inline:
        # Some envs mistakenly put a file path in BEN_MASTER_SYSTEM_PROMPT.
        maybe_path = Path(inline)
        if not maybe_path.is_absolute():
            maybe_path = _PROMPTS_DIR.parent / inline
        if inline.endswith((".txt", ".md")) and maybe_path.is_file():
            loaded = _read_prompt_file(maybe_path)
            if loaded:
                _log_prompt_source(f"BEN_MASTER_SYSTEM_PROMPT path {maybe_path}")
                return loaded
        if len(inline) > 200:
            _log_prompt_source("BEN_MASTER_SYSTEM_PROMPT env")
            return inline

    override = (BEN_MASTER_PROMPT_PATH or "").strip()
    if override:
        loaded = _read_prompt_file(Path(override))
        if loaded:
            _log_prompt_source(f"BEN_MASTER_PROMPT_PATH {override}")
            return loaded

    loaded = _read_prompt_file(_DEFAULT_MASTER_PATH)
    if loaded:
        _log_prompt_source(str(_DEFAULT_MASTER_PATH))
        return loaded

    stub = _read_prompt_file(_STUB_PROMPT_PATH)
    if stub:
        _log_prompt_source(f"public stub {_STUB_PROMPT_PATH}")
        return stub

    _log_prompt_source("inline fallback")
    return (
        "You are Sentient, an AI companion trained on Ben Warren's teaching. "
        "You are not Ben in person."
    )


def master_prompt_is_production_ready() -> bool:
    """
    True when the proprietary master prompt is available (not the public stub).

    Accepts BEN_MASTER_SYSTEM_PROMPT, BEN_MASTER_PROMPT_PATH, or the default v1 file.
    """
    if (BEN_MASTER_SYSTEM_PROMPT or "").strip():
        return True

    override = (BEN_MASTER_PROMPT_PATH or "").strip()
    if override:
        return bool(_read_prompt_file(Path(override)))

    return bool(_read_prompt_file(_DEFAULT_MASTER_PATH))


def format_onboarding_profile_block(onboarding: Optional[dict[str, Any]]) -> Optional[str]:
    """[BEN ONBOARDING] block. None until completed_at is set."""
    if not onboarding or not onboarding.get("completed_at"):
        return None

    def labeled(field: str, labels: dict[str, str]) -> str:
        key = onboarding.get(field)
        if not isinstance(key, str) or not key:
            return f"{field}: (unset)"
        gist = labels.get(key, key)
        return f"{field}: {key} ({gist})"

    experience = onboarding.get("prior_experience") or []
    if isinstance(experience, str):
        experience = [experience]
    exp_parts = []
    for key in experience:
        if isinstance(key, str):
            exp_parts.append(f"{key} ({PRIOR_EXPERIENCE_LABELS.get(key, key)})")
    experience_line = ", ".join(exp_parts) if exp_parts else "(unset)"

    lines = [
        "[BEN ONBOARDING]",
        labeled("path_stage", PATH_STAGE_LABELS),
        labeled("primary_reason", PRIMARY_REASON_LABELS),
        f"prior_experience: {experience_line}",
        labeled("language_preference", LANGUAGE_LABELS),
        labeled("desired_value", DESIRED_VALUE_LABELS),
        labeled("practice_time", PRACTICE_TIME_LABELS),
    ]
    tradition = onboarding.get("tradition_detail")
    if isinstance(tradition, str) and tradition.strip():
        lines.append(f"tradition_detail: {tradition.strip()}")
    version = onboarding.get("version")
    if isinstance(version, str) and version.strip():
        lines.append(f"version: {version.strip()}")
    lines.append(
        "Use these keys as facts about the user. Do not invent answers they did not give."
    )
    return "\n".join(lines)


def format_personalisation_rules(onboarding: Optional[dict[str, Any]]) -> Optional[str]:
    """Stage, language, tone, and meditation-name bias from the six answers."""
    if not onboarding or not onboarding.get("completed_at"):
        return None

    stage = onboarding.get("path_stage")
    language = onboarding.get("language_preference")
    desired = onboarding.get("desired_value")
    when = onboarding.get("practice_time")
    tradition = onboarding.get("tradition_detail")

    lines = ["[BEN PERSONALISATION]"]

    if stage in _STAGE_ONE_TWO:
        lines.append(
            "Stage: beginner / finding their way. Keep language simple. "
            "Prefer Watching the Mind and Watching the Body by name when suggesting practice."
        )
    elif stage == "been_on_path":
        lines.append(
            "Stage: books, retreats, glimpses. Inquiry is welcome. "
            "Prefer Where Is Awareness, Relaxing Back to Awareness, or Exploring the Is-ness of Awareness."
        )
    elif stage == "deep_in_it":
        lines.append(
            "Stage: significant realisations, integrating. "
            "Prefer Looking at Awareness Itself or Becoming Aware of Awareness. Do not talk down to them."
        )

    if language == "plain":
        lines.append(
            "Language: plain English only. Avoid jargon, Sanskrit, and Advaita terms unless the user uses them first."
        )
    elif language == "mindfulness":
        lines.append("Language: meditation and mindfulness terms are OK. Avoid dense non-dual jargon.")
    elif language == "nondual":
        lines.append("Language: non dual and Advaita terms are OK when they help.")
    elif language == "tradition":
        detail = tradition.strip() if isinstance(tradition, str) else ""
        if detail:
            lines.append(f"Language: they asked for their tradition ({detail}). Meet them there without syncretism.")
        else:
            lines.append("Language: they prefer a specific tradition. Ask which if it is not yet named.")
    elif language == "open":
        lines.append("Language: open. Match whatever register they use.")

    if desired == "guided_meditations":
        lines.append("Bias: offer a named practice more readily than long dialogue.")
    elif desired == "someone_to_talk":
        lines.append("Bias: stay in coaching dialogue; suggest a meditation only when it clearly helps.")
    elif desired == "understand_awareness":
        lines.append("Bias: point toward what awareness is, not toward collecting techniques.")
    elif desired == "support_difficulty":
        lines.append(
            "Bias: grounding and care. Prefer Watching the Body or Relaxing Back to Awareness. Do not minimise difficulty."
        )
    elif desired == "all_of_it":
        lines.append("Bias: full practice — dialogue and named meditations as needed.")

    if when == "morning":
        lines.append("Time: mornings. Watching the Mind is a natural daily anchor.")
    elif when == "during_day":
        lines.append("Time: short sessions during the day. Keep suggestions brief.")
    elif when == "evening":
        lines.append("Time: wind down. Watching the Body fits well.")
    elif when == "late_night":
        lines.append("Time: late night, things feel too much. Prefer Relaxing Back to Awareness. Keep the reply settling, not stimulating.")
    elif when == "whenever":
        lines.append("Time: whenever they remember. Offer short, usable suggestions.")

    if len(lines) == 1:
        return None
    return "\n".join(lines)


def build_companion_voice_parts(onboarding: Optional[dict[str, Any]]) -> list[str]:
    """Safety, session rules, master prompt, onboarding, personalisation. Base script is added in assemble."""
    parts = [
        SAFETY_POLICY.strip(),
        COMPANION_SESSION_RULES.strip(),
        load_master_system_prompt(),
    ]
    onboarding_block = format_onboarding_profile_block(onboarding)
    if onboarding_block:
        parts.append(onboarding_block)
    personalisation = format_personalisation_rules(onboarding)
    if personalisation:
        parts.append(personalisation)
    return parts


def assemble_companion_system_prompt(
    onboarding: Optional[dict[str, Any]] = None,
    *,
    base_script: Optional[str] = None,
    profile_memory_block: Optional[str] = None,
    retrieved_context: Optional[list[str]] = None,
) -> str:
    """
    Full companion system prompt.

    Precedence: safety → base script → companion session rules → master prompt →
    onboarding → personalisation → profile/memory → retrieval.
    """
    parts = [SAFETY_POLICY.strip()]
    if base_script and base_script.strip():
        parts.append(f"[Grounded Base Script]\n{base_script.strip()}")
    parts.append(COMPANION_SESSION_RULES.strip())
    parts.append(load_master_system_prompt())
    onboarding_block = format_onboarding_profile_block(onboarding)
    if onboarding_block:
        parts.append(onboarding_block)
    personalisation = format_personalisation_rules(onboarding)
    if personalisation:
        parts.append(personalisation)
    if profile_memory_block and profile_memory_block.strip():
        parts.append(profile_memory_block.strip())
    if retrieved_context:
        extra = "\n\n".join(item for item in retrieved_context if item)
        if extra:
            parts.append(f"[Additional context]\n{extra}")
    return "\n\n".join(parts)
