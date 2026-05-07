import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

APP_TITLE = "Sentient Backend"
APP_VERSION = "0.1.0"
MAX_MEMORY_MESSAGES = 8
DEFAULT_PROVIDER = os.getenv("AI_PROVIDER", "openai").lower()
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
AUTH_ENFORCED = os.getenv("AUTH_ENFORCED", "false").lower() in {"1", "true", "yes", "on"}
RAG_ENABLED = os.getenv("RAG_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
