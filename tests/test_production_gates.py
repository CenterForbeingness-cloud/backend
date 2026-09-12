"""Hardening Phase 1 items 1 to 7: production security boot gates."""

from unittest.mock import MagicMock, patch

import pytest

from app.production_gates import (
    assert_production_ai_credentials,
    assert_production_chat_token_enforced,
    assert_production_database_configured,
    assert_production_login_enforced,
    assert_production_master_prompt,
    assert_production_payment_stage,
    assert_production_phase1_security_gates,
    assert_production_rate_limit_enforced,
    is_production,
)


def test_is_production_false_by_default() -> None:
    with patch("app.config.ENVIRONMENT", "development"), patch(
        "app.config.RAILWAY_ENVIRONMENT", ""
    ):
        assert is_production() is False


def test_is_production_true_when_environment_production() -> None:
    with patch("app.config.ENVIRONMENT", "production"), patch(
        "app.config.RAILWAY_ENVIRONMENT", ""
    ):
        assert is_production() is True


def test_is_production_true_when_railway_production() -> None:
    with patch("app.config.ENVIRONMENT", "development"), patch(
        "app.config.RAILWAY_ENVIRONMENT", "production"
    ):
        assert is_production() is True


def test_assert_production_login_enforced_noop_in_development() -> None:
    with patch("app.production_gates.is_production", return_value=False), patch(
        "app.config.AUTH_ENFORCED", False
    ):
        assert_production_login_enforced()


def test_assert_production_login_enforced_fails_when_auth_off() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.AUTH_ENFORCED", False
    ), patch("app.config.SUPABASE_JWT_SECRET", "secret"):
        with pytest.raises(RuntimeError, match="AUTH_ENFORCED=true"):
            assert_production_login_enforced()


def test_assert_production_login_enforced_fails_without_jwt_secret() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.AUTH_ENFORCED", True
    ), patch("app.config.SUPABASE_JWT_SECRET", ""):
        with pytest.raises(RuntimeError, match="SUPABASE_JWT_SECRET"):
            assert_production_login_enforced()


def test_assert_production_login_enforced_passes_when_configured() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.AUTH_ENFORCED", True
    ), patch("app.config.SUPABASE_JWT_SECRET", "test-secret"):
        assert_production_login_enforced()


def test_assert_production_chat_token_enforced_noop_in_development() -> None:
    with patch("app.production_gates.is_production", return_value=False), patch(
        "app.config.CHAT_TOKEN_ENFORCED", False
    ):
        assert_production_chat_token_enforced()


def test_assert_production_chat_token_enforced_fails_when_off() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.CHAT_TOKEN_ENFORCED", False
    ), patch("app.config.CHAT_TOKEN_SECRET", "secret"):
        with pytest.raises(RuntimeError, match="CHAT_TOKEN_ENFORCED=true"):
            assert_production_chat_token_enforced()


def test_assert_production_chat_token_enforced_fails_without_secret() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.CHAT_TOKEN_ENFORCED", True
    ), patch("app.config.CHAT_TOKEN_SECRET", ""):
        with pytest.raises(RuntimeError, match="CHAT_TOKEN_SECRET"):
            assert_production_chat_token_enforced()


def test_assert_production_chat_token_enforced_passes_when_configured() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.CHAT_TOKEN_ENFORCED", True
    ), patch("app.config.CHAT_TOKEN_SECRET", "chat-secret"):
        assert_production_chat_token_enforced()


def test_assert_production_rate_limit_enforced_noop_in_development() -> None:
    with patch("app.production_gates.is_production", return_value=False), patch(
        "app.config.RATE_LIMIT_ENABLED", False
    ):
        assert_production_rate_limit_enforced()


def test_assert_production_rate_limit_enforced_fails_when_off() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.RATE_LIMIT_ENABLED", False
    ):
        with pytest.raises(RuntimeError, match="RATE_LIMIT_ENABLED=true"):
            assert_production_rate_limit_enforced()


def test_assert_production_rate_limit_enforced_passes_when_on() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.RATE_LIMIT_ENABLED", True
    ):
        assert_production_rate_limit_enforced()


def test_assert_production_database_configured_noop_in_development() -> None:
    with patch("app.production_gates.is_production", return_value=False), patch(
        "app.config.SUPABASE_DB_URL", None
    ):
        assert_production_database_configured()


def test_assert_production_database_configured_fails_without_url() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.SUPABASE_DB_URL", ""
    ):
        with pytest.raises(RuntimeError, match="SUPABASE_DB_URL"):
            assert_production_database_configured()


def test_assert_production_database_configured_fails_on_bad_connection() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.SUPABASE_DB_URL", "postgresql://bad"
    ), patch("psycopg.connect", side_effect=OSError("refused")):
        with pytest.raises(RuntimeError, match="database connection failed"):
            assert_production_database_configured()


def test_assert_production_database_configured_passes_when_reachable() -> None:
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None
    mock_cur = MagicMock()
    mock_cur.__enter__.return_value = mock_cur
    mock_cur.__exit__.return_value = None
    mock_conn.cursor.return_value = mock_cur

    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.SUPABASE_DB_URL", "postgresql://ok"
    ), patch("psycopg.connect", return_value=mock_conn) as connect:
        assert_production_database_configured()
        connect.assert_called_once()
        mock_cur.execute.assert_called_once_with("SELECT 1")


def test_assert_production_phase1_security_gates_runs_all_seven() -> None:
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None
    mock_cur = MagicMock()
    mock_cur.__enter__.return_value = mock_cur
    mock_cur.__exit__.return_value = None
    mock_conn.cursor.return_value = mock_cur

    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.AUTH_ENFORCED", True
    ), patch("app.config.SUPABASE_JWT_SECRET", "jwt"), patch(
        "app.config.CHAT_TOKEN_ENFORCED", True
    ), patch("app.config.CHAT_TOKEN_SECRET", "chat"), patch(
        "app.config.RATE_LIMIT_ENABLED", True
    ), patch("app.config.SUPABASE_DB_URL", "postgresql://ok"), patch(
        "psycopg.connect", return_value=mock_conn
    ), patch("app.config.DEFAULT_PROVIDER", "openai"), patch(
        "app.config.OPENAI_API_KEY", "sk-test"
    ), patch("app.config.PAYMENT_STAGE", "test"), patch(
        "app.config.STRIPE_SECRET_KEY", "sk_test_abc"
    ), patch("app.config.STRIPE_WEBHOOK_SECRET", "whsec_abc"), patch(
        "app.coaching_voice.master_prompt_is_production_ready", return_value=True
    ):
        assert_production_phase1_security_gates()


def test_assert_production_phase1_security_gates_fails_on_rate_limit() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.AUTH_ENFORCED", True
    ), patch("app.config.SUPABASE_JWT_SECRET", "jwt"), patch(
        "app.config.CHAT_TOKEN_ENFORCED", True
    ), patch("app.config.CHAT_TOKEN_SECRET", "chat"), patch(
        "app.config.RATE_LIMIT_ENABLED", False
    ):
        with pytest.raises(RuntimeError, match="RATE_LIMIT_ENABLED=true"):
            assert_production_phase1_security_gates()


def test_assert_production_ai_credentials_noop_in_development() -> None:
    with patch("app.production_gates.is_production", return_value=False), patch(
        "app.config.OPENAI_API_KEY", ""
    ):
        assert_production_ai_credentials()


def test_assert_production_ai_credentials_fails_without_openai_key() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.DEFAULT_PROVIDER", "openai"
    ), patch("app.config.OPENAI_API_KEY", ""):
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            assert_production_ai_credentials()


def test_assert_production_ai_credentials_fails_without_anthropic_key() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.DEFAULT_PROVIDER", "anthropic"
    ), patch("app.config.ANTHROPIC_API_KEY", ""):
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            assert_production_ai_credentials()


def test_assert_production_ai_credentials_passes_for_openai() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.DEFAULT_PROVIDER", "openai"
    ), patch("app.config.OPENAI_API_KEY", "sk-test"):
        assert_production_ai_credentials()


def test_assert_production_ai_credentials_passes_for_anthropic() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.DEFAULT_PROVIDER", "claude"
    ), patch("app.config.ANTHROPIC_API_KEY", "ant-test"):
        assert_production_ai_credentials()


def test_assert_production_ai_credentials_rejects_unknown_provider() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.DEFAULT_PROVIDER", "unknown"
    ):
        with pytest.raises(RuntimeError, match="unsupported AI_PROVIDER"):
            assert_production_ai_credentials()


def test_assert_production_payment_stage_noop_in_development() -> None:
    with patch("app.production_gates.is_production", return_value=False), patch(
        "app.config.PAYMENT_STAGE", ""
    ):
        assert_production_payment_stage()


def test_assert_production_payment_stage_requires_stage() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.PAYMENT_STAGE", ""
    ):
        with pytest.raises(RuntimeError, match="PAYMENT_STAGE"):
            assert_production_payment_stage()


def test_assert_production_payment_stage_requires_secret() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.PAYMENT_STAGE", "test"
    ), patch("app.config.STRIPE_SECRET_KEY", ""):
        with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
            assert_production_payment_stage()


def test_assert_production_payment_stage_rejects_mismatched_key() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.PAYMENT_STAGE", "live"
    ), patch("app.config.STRIPE_SECRET_KEY", "sk_test_abc"), patch(
        "app.config.STRIPE_WEBHOOK_SECRET", "whsec_abc"
    ):
        with pytest.raises(RuntimeError, match="sk_live_"):
            assert_production_payment_stage()


def test_assert_production_payment_stage_requires_webhook_secret() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.PAYMENT_STAGE", "test"
    ), patch("app.config.STRIPE_SECRET_KEY", "sk_test_abc"), patch(
        "app.config.STRIPE_WEBHOOK_SECRET", ""
    ):
        with pytest.raises(RuntimeError, match="STRIPE_WEBHOOK_SECRET"):
            assert_production_payment_stage()


def test_assert_production_payment_stage_passes_for_test() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.PAYMENT_STAGE", "test"
    ), patch("app.config.STRIPE_SECRET_KEY", "sk_test_abc"), patch(
        "app.config.STRIPE_WEBHOOK_SECRET", "whsec_abc"
    ):
        assert_production_payment_stage()


def test_assert_production_payment_stage_passes_for_live() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.PAYMENT_STAGE", "live"
    ), patch("app.config.STRIPE_SECRET_KEY", "sk_live_abc"), patch(
        "app.config.STRIPE_WEBHOOK_SECRET", "whsec_live"
    ):
        assert_production_payment_stage()


def test_assert_production_master_prompt_noop_in_development() -> None:
    with patch("app.production_gates.is_production", return_value=False), patch(
        "app.coaching_voice.master_prompt_is_production_ready", return_value=False
    ):
        assert_production_master_prompt()


def test_assert_production_master_prompt_fails_without_real_prompt() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.coaching_voice.master_prompt_is_production_ready", return_value=False
    ):
        with pytest.raises(RuntimeError, match="master coaching prompt"):
            assert_production_master_prompt()


def test_assert_production_master_prompt_passes_when_ready() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.coaching_voice.master_prompt_is_production_ready", return_value=True
    ):
        assert_production_master_prompt()
