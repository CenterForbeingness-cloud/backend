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
