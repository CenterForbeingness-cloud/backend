from collections import defaultdict, deque
from typing import Deque, Dict, List, Protocol

from app.config import MAX_MEMORY_MESSAGES, SUPABASE_DB_URL, logger


class ChatStore(Protocol):
    def ensure_session(self, session_id: str) -> None: ...

    def get_history(self, session_id: str, limit: int) -> List[Dict[str, str]]: ...

    def list_messages(self, session_id: str, limit: int) -> List[Dict[str, str]]: ...

    def append_message(self, session_id: str, role: str, content: str) -> None: ...

    def clear_session(self, session_id: str) -> None: ...


class InMemoryChatStore:
    def __init__(self, max_messages: int) -> None:
        self.max_messages = max_messages
        self.memory: Dict[str, Deque[Dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )

    def get_history(self, session_id: str, limit: int) -> List[Dict[str, str]]:
        history = list(self.memory[session_id])
        return history[-limit:]

    def list_messages(self, session_id: str, limit: int) -> List[Dict[str, str]]:
        return self.get_history(session_id, limit)

    def ensure_session(self, session_id: str) -> None:
        _ = self.memory[session_id]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        self.memory[session_id].append({"role": role, "content": content})

    def clear_session(self, session_id: str) -> None:
        self.memory.pop(session_id, None)


class PostgresChatStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)

    def init(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created_at
                ON chat_messages(session_id, created_at, id)
                """
            )

    def get_history(self, session_id: str, limit: int) -> List[Dict[str, str]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content
                FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()

        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    def list_messages(self, session_id: str, limit: int) -> List[Dict[str, str]]:
        return self.get_history(session_id, limit)

    def ensure_session(self, session_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (session_id, updated_at)
                VALUES (%s, timezone('utc', now()))
                ON CONFLICT (session_id)
                DO UPDATE SET updated_at = EXCLUDED.updated_at
                """,
                (session_id,),
            )

    def append_message(self, session_id: str, role: str, content: str) -> None:
        self.ensure_session(session_id)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_messages (session_id, role, content)
                VALUES (%s, %s, %s)
                """,
                (session_id, role, content),
            )

    def clear_session(self, session_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))


def build_chat_store() -> ChatStore:
    if not SUPABASE_DB_URL:
        logger.info("SUPABASE_DB_URL not set; using in-memory chat storage")
        return InMemoryChatStore(MAX_MEMORY_MESSAGES)

    store = PostgresChatStore(SUPABASE_DB_URL)
    try:
        store.init()
    except Exception:
        logger.exception("Failed to initialize Postgres chat storage; using in-memory fallback")
        return InMemoryChatStore(MAX_MEMORY_MESSAGES)

    logger.info("Using Postgres chat storage")
    return store
