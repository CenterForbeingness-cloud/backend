import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

APP_TITLE = "Sentient Backend"
APP_VERSION = "0.1.0"
MAX_MEMORY_MESSAGES = 8
SCHEDULE_HISTORY_MESSAGES = int(os.getenv("SCHEDULE_HISTORY_MESSAGES", "6"))
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
CHAT_MODEL_SCHEDULE = os.getenv("CHAT_MODEL_SCHEDULE", CHAT_MODEL)
SCHEDULE_SCRIPT_ENGINE = os.getenv("SCHEDULE_SCRIPT_ENGINE", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEFAULT_PROVIDER = os.getenv("AI_PROVIDER", "openai").lower()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
AUTH_ENFORCED = os.getenv("AUTH_ENFORCED", "false").lower() in {"1", "true", "yes", "on"}
CHAT_TOKEN_ENFORCED = os.getenv("CHAT_TOKEN_ENFORCED", "false").lower() in {"1", "true", "yes", "on"}
RAG_ENABLED = os.getenv("RAG_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "sentient-content")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
_raw_cors = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS: list[str] = [o.strip() for o in _raw_cors.split(",") if o.strip()]
_chat_token_secret = (os.getenv("CHAT_TOKEN_SECRET") or "").strip()
CHAT_TOKEN_SECRET = _chat_token_secret or (SUPABASE_JWT_SECRET or "")
CHAT_TOKEN_TTL_SECONDS = int(os.getenv("CHAT_TOKEN_TTL_SECONDS", "900"))

# Billing (Stripe)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Per-course Stripe Price IDs (fallback when course_products rows are not seeded yet).
STRIPE_PRICE_BY_COURSE_SLUG: dict[str, str] = {
    slug: price_id
    for slug, env_key in (
        ("week-zero-reset", "STRIPE_PRICE_WEEK_ZERO_RESET"),
        ("deep-calm-protocol", "STRIPE_PRICE_DEEP_CALM"),
        ("focus-discipline", "STRIPE_PRICE_FOCUS_DISCIPLINE"),
        ("starter-bundle", "STRIPE_PRICE_STARTER_BUNDLE"),
    )
    if (price_id := os.getenv(env_key, "").strip())
}

# Quotas & Rate Limiting
FAIR_USE_LIMIT = int(os.getenv("FAIR_USE_LIMIT", "100"))  # messages per period
QUOTA_RESET_PERIOD_HOURS = int(os.getenv("QUOTA_RESET_PERIOD_HOURS", "24"))
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RATE_LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "30/minute")
RATE_LIMIT_SESSIONS = os.getenv("RATE_LIMIT_SESSIONS", "60/minute")
RATE_LIMIT_AUTH = os.getenv("RATE_LIMIT_AUTH", "10/minute")
RATE_LIMIT_BILLING = os.getenv("RATE_LIMIT_BILLING", "15/minute")

# Admin & 2FA
ADMIN_2FA_ISSUER = os.getenv("ADMIN_2FA_ISSUER", "Sentient")
ADMIN_2FA_WINDOW = int(os.getenv("ADMIN_2FA_WINDOW", "1"))  # TOTP time window tolerance
# Comma-separated IPs allowed to hit /admin/* (empty = allow all, for local dev)
ADMIN_ALLOWED_IPS_RAW = os.getenv("ADMIN_ALLOWED_IPS", "").strip()
ADMIN_UI_URL = os.getenv(
    "ADMIN_UI_URL",
    "https://backend-production-2df9.up.railway.app/admin/ui",
).strip()
ADMIN_INVITE_FROM_EMAIL = os.getenv("ADMIN_INVITE_FROM_EMAIL", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()

# Voice (MVP Launch) — POST /chat/voice
VOICE_ENABLED = os.getenv("VOICE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
VOICE_MAX_RECORDING_SEC = int(os.getenv("VOICE_MAX_RECORDING_SEC", "90"))
VOICE_DAILY_SECONDS_CAP = int(os.getenv("VOICE_DAILY_SECONDS_CAP", "600"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID_DEFAULT = os.getenv("ELEVENLABS_VOICE_ID_DEFAULT", "")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
