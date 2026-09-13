from __future__ import annotations


def is_production() -> bool:
    """
    True when this process is meant to run as production.

    ENVIRONMENT / SENTIENT_ENV: set to production on Railway.
    RAILWAY_ENVIRONMENT: Railway often sets this to production for the live service.
    Default is development so local boots stay open unless you opt into production.
    """
    from app import config

    env = (getattr(config, "ENVIRONMENT", "") or "").strip().lower()
    if env in {"production", "prod"}:
        return True

    railway = (getattr(config, "RAILWAY_ENVIRONMENT", "") or "").strip().lower()
    if railway in {"production", "prod"}:
        return True

    return False


def assert_production_login_enforced() -> None:
    """
    Hardening Phase 1 item 1.

    In production, AUTH_ENFORCED must be true and SUPABASE_JWT_SECRET must be set.
    Local development (not production) may keep AUTH_ENFORCED=false.
    """
    from app import config

    if not is_production():
        return

    if not config.AUTH_ENFORCED:
        raise RuntimeError(
            "Production requires AUTH_ENFORCED=true. "
            "Set AUTH_ENFORCED=true on the host, or set ENVIRONMENT=development for local use."
        )

    if not (config.SUPABASE_JWT_SECRET or "").strip():
        raise RuntimeError(
            "Production requires SUPABASE_JWT_SECRET when login enforcement is on."
        )


def assert_production_chat_token_enforced() -> None:
    """
    Hardening Phase 1 item 2.

    In production, CHAT_TOKEN_ENFORCED must be true and CHAT_TOKEN_SECRET must be set
    (CHAT_TOKEN_SECRET may fall back to SUPABASE_JWT_SECRET in config).
    """
    from app import config

    if not is_production():
        return

    if not config.CHAT_TOKEN_ENFORCED:
        raise RuntimeError(
            "Production requires CHAT_TOKEN_ENFORCED=true. "
            "Set CHAT_TOKEN_ENFORCED=true on the host, or set ENVIRONMENT=development for local use."
        )

    if not (config.CHAT_TOKEN_SECRET or "").strip():
        raise RuntimeError(
            "Production requires CHAT_TOKEN_SECRET (or SUPABASE_JWT_SECRET as fallback) "
            "when chat token enforcement is on."
        )


def assert_production_rate_limit_enforced() -> None:
    """
    Hardening Phase 1 item 3.

    In production, RATE_LIMIT_ENABLED must be true.
    """
    from app import config

    if not is_production():
        return

    if not config.RATE_LIMIT_ENABLED:
        raise RuntimeError(
            "Production requires RATE_LIMIT_ENABLED=true. "
            "Set RATE_LIMIT_ENABLED=true on the host, or set ENVIRONMENT=development for local use."
        )


def assert_production_database_configured() -> None:
    """
    Hardening Phase 1 item 4.

    In production, SUPABASE_DB_URL must be set and accept a simple connection check.
    Local development may omit the database URL.
    """
    from app import config

    if not is_production():
        return

    db_url = (config.SUPABASE_DB_URL or "").strip()
    if not db_url:
        raise RuntimeError(
            "Production requires SUPABASE_DB_URL. "
            "Set the Postgres connection string on the host, or set ENVIRONMENT=development for local use."
        )

    try:
        import psycopg

        with psycopg.connect(
            db_url,
            connect_timeout=5,
            prepare_threshold=None,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:
        raise RuntimeError(
            "Production database connection failed. "
            f"Check SUPABASE_DB_URL and network access. Detail: {exc}"
        ) from exc


def assert_production_ai_credentials() -> None:
    """
    Hardening Phase 1 item 5.

    In production, the default chat provider must have an API key set.
    Local development may omit keys (chat then falls back until Phase 2 removes that).
    """
    from app import config

    if not is_production():
        return

    provider = (config.DEFAULT_PROVIDER or "openai").strip().lower()

    if provider in {"openai", "gpt", "oai"}:
        if not (config.OPENAI_API_KEY or "").strip():
            raise RuntimeError(
                "Production requires OPENAI_API_KEY when AI_PROVIDER is openai. "
                "Set OPENAI_API_KEY on the host, or set ENVIRONMENT=development for local use."
            )
        return

    if provider in {"anthropic", "claude"}:
        if not (config.ANTHROPIC_API_KEY or "").strip():
            raise RuntimeError(
                "Production requires ANTHROPIC_API_KEY when AI_PROVIDER is anthropic. "
                "Set ANTHROPIC_API_KEY on the host, or set ENVIRONMENT=development for local use."
            )
        return

    raise RuntimeError(
        f"Production has unsupported AI_PROVIDER={provider!r}. "
        "Use openai or anthropic."
    )


def _stripe_key_mode(secret_key: str) -> str:
    key = (secret_key or "").strip()
    if key.startswith("sk_live_"):
        return "live"
    if key.startswith("sk_test_"):
        return "test"
    return "unset"


def assert_production_payment_stage() -> None:
    """
    Hardening Phase 1 item 6.

    In production you must declare PAYMENT_STAGE=test or PAYMENT_STAGE=live.
    Stripe secret and webhook secret must be present, and the secret key must match that stage.
    Soft launch uses test. Public money uses live.
    """
    from app import config

    if not is_production():
        return

    stage = (config.PAYMENT_STAGE or "").strip().lower()
    if stage not in {"test", "live"}:
        raise RuntimeError(
            "Production requires PAYMENT_STAGE=test or PAYMENT_STAGE=live. "
            "Use test for soft launch rehearsals. Use live for real charges."
        )

    secret = (config.STRIPE_SECRET_KEY or "").strip()
    if not secret:
        raise RuntimeError(
            "Production requires STRIPE_SECRET_KEY (or STRIPE_API_KEY). "
            "Set it on the host to match PAYMENT_STAGE."
        )

    mode = _stripe_key_mode(secret)
    if mode != stage:
        raise RuntimeError(
            f"PAYMENT_STAGE={stage} but STRIPE_SECRET_KEY looks like {mode!r}. "
            "Use sk_test_... for test and sk_live_... for live."
        )

    if not (config.STRIPE_WEBHOOK_SECRET or "").strip():
        raise RuntimeError(
            "Production requires STRIPE_WEBHOOK_SECRET when payments are configured. "
            "Use the Dashboard webhook secret for this stage, not a temporary CLI secret."
        )


def assert_production_master_prompt() -> None:
    """
    Hardening Phase 1 item 7.

    Companion coaching depends on the Ben master prompt. Production must have the real
    prompt via env or the v1 file. The public stub is not enough for production.
    """
    if not is_production():
        return

    from app.coaching_voice import master_prompt_is_production_ready

    if not master_prompt_is_production_ready():
        raise RuntimeError(
            "Production requires the Ben master coaching prompt. "
            "Set BEN_MASTER_SYSTEM_PROMPT, or BEN_MASTER_PROMPT_PATH to the v1 file, "
            "or place prompts/ben_master_system_prompt_v1.txt on the server. "
            "The public stub is not enough for production."
        )


def assert_production_persistent_chat_store(chat_store) -> None:
    """
    Hardening Phase 4.

    Production must use Postgres chat storage. In-memory is for local development only.
    """
    if not is_production():
        return

    from app.storage import PostgresChatStore

    if not isinstance(chat_store, PostgresChatStore):
        raise RuntimeError(
            "Production requires Postgres chat storage. "
            f"Got {type(chat_store).__name__}. Refusing in-memory or other fallbacks."
        )


def assert_production_course_catalog_from_database() -> None:
    """
    Hardening Phase 4.

    Production must read the course catalog from Postgres. A successful query is required
    (an empty published list is allowed). Filesystem course folders are not a production source.
    """
    if not is_production():
        return

    from app import config

    if not (config.SUPABASE_DB_URL or "").strip():
        raise RuntimeError(
            "Production requires SUPABASE_DB_URL for the course catalog."
        )

    try:
        from app.courses import _list_courses_from_db

        _list_courses_from_db()
    except Exception as exc:
        raise RuntimeError(
            "Production course catalog database query failed. "
            "Apply course catalog SQL and refuse filesystem fallback. "
            f"Detail: {exc}"
        ) from exc


def assert_production_phase1_security_gates() -> None:
    """Run Phase 1 items 1 to 7 at startup."""
    assert_production_login_enforced()
    assert_production_chat_token_enforced()
    assert_production_rate_limit_enforced()
    assert_production_database_configured()
    assert_production_ai_credentials()
    assert_production_payment_stage()
    assert_production_master_prompt()


def assert_production_phase4_storage_gates(chat_store) -> None:
    """Run Phase 4 persistent storage gates at startup."""
    assert_production_persistent_chat_store(chat_store)
    assert_production_course_catalog_from_database()
