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
