from app.coaching_voice import (
    SAFETY_POLICY,
    assemble_companion_system_prompt,
    build_companion_voice_parts,
    format_onboarding_profile_block,
    format_personalisation_rules,
    load_master_system_prompt,
)


COMPLETED = {
    "path_stage": "finding_my_way",
    "primary_reason": "searching_deeper",
    "prior_experience": ["yoga_breathwork", "therapy_psychology"],
    "language_preference": "plain",
    "desired_value": "someone_to_talk",
    "practice_time": "morning",
    "completed_at": "2026-08-22T00:00:00+00:00",
    "version": "cfb_june_2026",
}


def test_incomplete_onboarding_returns_no_block():
    assert format_onboarding_profile_block(None) is None
    assert format_onboarding_profile_block({"path_stage": "just_starting"}) is None
    assert format_personalisation_rules({"path_stage": "just_starting"}) is None


def test_completed_onboarding_block_includes_keys():
    block = format_onboarding_profile_block(COMPLETED)
    assert block is not None
    assert "[BEN ONBOARDING]" in block
    assert "path_stage: finding_my_way" in block
    assert "primary_reason: searching_deeper" in block
    assert "yoga_breathwork" in block
    assert "language_preference: plain" in block
    assert "desired_value: someone_to_talk" in block
    assert "practice_time: morning" in block
    assert "cfb_june_2026" in block


def test_plain_vs_nondual_language_rules_differ():
    plain = format_personalisation_rules(COMPLETED)
    nondual = format_personalisation_rules(
        {**COMPLETED, "language_preference": "nondual"}
    )
    assert plain is not None and nondual is not None
    assert "plain English" in plain
    assert "Avoid jargon" in plain
    assert "non dual" in nondual.lower() or "Advaita" in nondual
    assert "plain English" not in nondual


def test_beginner_stage_names_watching_the_mind():
    rules = format_personalisation_rules(
        {**COMPLETED, "path_stage": "just_starting"}
    )
    assert rules is not None
    assert "Watching the Mind" in rules


def test_assemble_includes_companion_session_greeting_rules():
    prompt = assemble_companion_system_prompt(COMPLETED)
    assert "[COMPANION SESSION]" in prompt
    assert "not small talk" in prompt
    assert "Who are you today?" in prompt
    assert "Do not chase the tangent" in prompt


def test_safety_and_transparency_always_present():
    parts = build_companion_voice_parts(None)
    joined = "\n\n".join(parts)
    assert "[SAFETY]" in joined
    assert "[COMPANION SESSION]" in joined
    assert "emergency services" in SAFETY_POLICY.lower() or "emergency" in joined.lower()
    assert "not Ben" in joined
    assert "diagnose" in joined.lower()


def test_master_prompt_loads_without_network():
    prompt = load_master_system_prompt()
    assert "Sentient" in prompt
    assert "not Ben" in prompt.lower() or "not Ben" in prompt


def test_incomplete_voice_parts_omit_onboarding_block():
    parts = build_companion_voice_parts({"path_stage": "just_starting"})
    joined = "\n".join(parts)
    assert "[BEN ONBOARDING]" not in joined
    assert "[BEN PERSONALISATION]" not in joined


def test_completed_voice_parts_include_onboarding():
    parts = build_companion_voice_parts(COMPLETED)
    joined = "\n".join(parts)
    assert "[BEN ONBOARDING]" in joined
    assert "[BEN PERSONALISATION]" in joined
    assert "searching_deeper" in joined


def test_assemble_incomplete_omits_onboarding_keeps_safety():
    prompt = assemble_companion_system_prompt(
        {"path_stage": "just_starting"},
        base_script="Stay grounded.",
    )
    assert "[SAFETY]" in prompt
    assert "emergency" in prompt.lower()
    assert "not Ben" in prompt
    assert "[Grounded Base Script]" in prompt
    assert "[BEN ONBOARDING]" not in prompt
    assert "calm meditation assistant" not in prompt


def test_assemble_completed_includes_keys_and_disclosure():
    prompt = assemble_companion_system_prompt(COMPLETED, base_script="Stay grounded.")
    assert "path_stage: finding_my_way" in prompt
    assert "searching_deeper" in prompt
    assert "[BEN PERSONALISATION]" in prompt
    assert "Are you talking to Ben" in prompt or "not Ben" in prompt
