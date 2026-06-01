from app.user_profile import UserProfile, format_profile_system_block, profile_has_launch_memory


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
