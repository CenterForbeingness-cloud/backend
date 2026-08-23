from datetime import datetime, timezone

import pytest

from app.user_profile import (
    UserProfile,
    format_profile_system_block,
    merge_ben_onboarding,
    missing_ben_onboarding_fields,
    normalize_prior_experience,
    profile_ben_onboarding_complete,
    profile_has_launch_memory,
    validate_ben_onboarding_complete,
    validate_ben_onboarding_patch,
    BenOnboardingValidationError,
)


def test_format_profile_system_block_includes_goals():
    profile = UserProfile(
        user_id="u1",
        primary_goal="Build a startup",
        current_focus="Launching MVP",
        motivation_type="Accountability",
    )
    block = format_profile_system_block(profile)
    assert block is not None
    assert "Primary goal: Build a startup" in block
    assert "Current focus: Launching MVP" in block
    assert "[USER PROFILE]" in block


def test_format_profile_system_block_empty_returns_none():
    assert format_profile_system_block(UserProfile(user_id="u1")) is None
    assert format_profile_system_block(None) is None


def test_profile_has_launch_memory():
    assert profile_has_launch_memory(None) is False
    assert profile_has_launch_memory(UserProfile(user_id="u1")) is False
    assert profile_has_launch_memory(
        UserProfile(user_id="u1", primary_goal="Exercise more")
    )


def test_incomplete_onboarding_is_not_complete():
    assert profile_ben_onboarding_complete(None) is False
    assert profile_ben_onboarding_complete(UserProfile(user_id="u1")) is False
    assert profile_ben_onboarding_complete(
        UserProfile(
            user_id="u1",
            ben_onboarding={"path_stage": "just_starting"},
        )
    ) is False


def test_completed_onboarding_flag_requires_completed_at():
    assert profile_ben_onboarding_complete(
        UserProfile(
            user_id="u1",
            ben_onboarding={
                "path_stage": "just_starting",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    )


def test_partial_merge_does_not_wipe_other_answers():
    existing = {
        "path_stage": "just_starting",
        "primary_reason": "searching_deeper",
    }
    merged = merge_ben_onboarding(existing, {"practice_time": "morning"})
    assert merged["path_stage"] == "just_starting"
    assert merged["primary_reason"] == "searching_deeper"
    assert merged["practice_time"] == "morning"


def test_partial_merge_keeps_completed_at():
    existing = {
        "path_stage": "been_on_path",
        "completed_at": "2026-08-01T00:00:00+00:00",
        "version": "cfb_june_2026",
    }
    merged = merge_ben_onboarding(existing, {"path_stage": "deep_in_it"})
    assert merged["path_stage"] == "deep_in_it"
    assert merged["completed_at"] == "2026-08-01T00:00:00+00:00"
    assert merged["version"] == "cfb_june_2026"


def test_starting_fresh_clears_other_prior_experience():
    keys = normalize_prior_experience(
        ["yoga_breathwork", "starting_fresh", "therapy_psychology"]
    )
    assert keys == ["starting_fresh"]


def test_invalid_option_key_rejected():
    with pytest.raises(BenOnboardingValidationError, match="path_stage"):
        validate_ben_onboarding_patch({"path_stage": "enlightened"})


def test_complete_rejects_missing_keys():
    with pytest.raises(BenOnboardingValidationError, match="Missing onboarding answers"):
        validate_ben_onboarding_complete({"path_stage": "just_starting"})
    missing = missing_ben_onboarding_fields({"path_stage": "just_starting"})
    assert "primary_reason" in missing
    assert "practice_time" in missing
    assert "prior_experience" in missing


def test_complete_accepts_all_six_and_normalizes_q3():
    payload = validate_ben_onboarding_complete(
        {
            "path_stage": "finding_my_way",
            "primary_reason": "peace_less_stress",
            "prior_experience": ["mindfulness_apps", "starting_fresh"],
            "language_preference": "plain",
            "desired_value": "someone_to_talk",
            "practice_time": "evening",
            "tradition_detail": "should be cleared",
        }
    )
    assert payload["prior_experience"] == ["starting_fresh"]
    assert payload["tradition_detail"] is None
