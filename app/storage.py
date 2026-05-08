from collections import defaultdict, deque
from typing import Deque, Dict, List, Protocol

from app.config import MAX_MEMORY_MESSAGES, SUPABASE_DB_URL, logger


class SessionAccessError(Exception):
    pass


class ChatStore(Protocol):
    def ensure_session(self, session_id: str, user_id: str | None = None) -> None: ...

    def get_history(
        self, session_id: str, limit: int, user_id: str | None = None
    ) -> List[Dict[str, str]]: ...

    def list_messages(
        self, session_id: str, limit: int, user_id: str | None = None
    ) -> List[Dict[str, str]]: ...

    def append_message(
        self, session_id: str, role: str, content: str, user_id: str | None = None
    ) -> None: ...

    def clear_session(self, session_id: str, user_id: str | None = None) -> None: ...


class InMemoryChatStore:
    def __init__(self, max_messages: int) -> None:
        self.max_messages = max_messages
        self.session_owners: Dict[str, str | None] = {}
        self.memory: Dict[str, Deque[Dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )

    def _assert_access(self, session_id: str, user_id: str | None) -> None:
        if user_id is None:
            return

        owner_id = self.session_owners.get(session_id)
        if owner_id is not None and owner_id != user_id:
            raise SessionAccessError("Session does not belong to the current user")

    def get_history(
        self, session_id: str, limit: int, user_id: str | None = None
    ) -> List[Dict[str, str]]:
        self._assert_access(session_id, user_id)
        history = list(self.memory[session_id])
        return history[-limit:]

    def list_messages(
        self, session_id: str, limit: int, user_id: str | None = None
    ) -> List[Dict[str, str]]:
        return self.get_history(session_id, limit, user_id)

    def ensure_session(self, session_id: str, user_id: str | None = None) -> None:
        self._assert_access(session_id, user_id)
        if session_id not in self.session_owners:
            self.session_owners[session_id] = user_id
        elif self.session_owners[session_id] is None and user_id is not None:
            self.session_owners[session_id] = user_id
        _ = self.memory[session_id]

    def append_message(
        self, session_id: str, role: str, content: str, user_id: str | None = None
    ) -> None:
        self.ensure_session(session_id, user_id)
        self.memory[session_id].append({"role": role, "content": content})

    def clear_session(self, session_id: str, user_id: str | None = None) -> None:
        self._assert_access(session_id, user_id)
        self.session_owners.pop(session_id, None)
        self.memory.pop(session_id, None)


class PostgresChatStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)

    def init(self) -> None:
        """Verify the required tables exist. Schema is managed externally via
        backend/sql/supabase_chat_rls.sql — do NOT run DDL here because the
        pooler role lacks the privileges needed to reference auth.users."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('chat_sessions', 'chat_messages')
                """
            )
            found = cur.fetchall()
            if len(found) < 2:
                raise RuntimeError(
                    "Required tables 'chat_sessions' and/or 'chat_messages' not found in the "
                    "database. Run backend/sql/supabase_chat_rls.sql in Supabase SQL Editor first."
                )

    def _assert_access(self, cur, session_id: str, user_id: str | None) -> None:
        if user_id is None:
            return

        cur.execute(
            """
            SELECT user_id
            FROM chat_sessions
            WHERE session_id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return

        owner_id = row[0]
        if owner_id is not None and str(owner_id) != user_id:
            raise SessionAccessError("Session does not belong to the current user")

    def get_history(
        self, session_id: str, limit: int, user_id: str | None = None
    ) -> List[Dict[str, str]]:
        with self._connect() as conn, conn.cursor() as cur:
            self._assert_access(cur, session_id, user_id)
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

    def list_messages(
        self, session_id: str, limit: int, user_id: str | None = None
    ) -> List[Dict[str, str]]:
        return self.get_history(session_id, limit, user_id)

    def ensure_session(self, session_id: str, user_id: str | None = None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (session_id, user_id, updated_at)
                VALUES (%s, %s, timezone('utc', now()))
                ON CONFLICT (session_id)
                DO UPDATE SET
                    updated_at = EXCLUDED.updated_at,
                    user_id = COALESCE(chat_sessions.user_id, EXCLUDED.user_id)
                RETURNING user_id
                """,
                (session_id, user_id),
            )
            owner_id = cur.fetchone()[0]
            if user_id is not None and owner_id is not None and str(owner_id) != user_id:
                raise SessionAccessError("Session does not belong to the current user")

    def append_message(
        self, session_id: str, role: str, content: str, user_id: str | None = None
    ) -> None:
        self.ensure_session(session_id, user_id)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_messages (session_id, role, content)
                VALUES (%s, %s, %s)
                """,
                (session_id, role, content),
            )

    def clear_session(self, session_id: str, user_id: str | None = None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            self._assert_access(cur, session_id, user_id)
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
