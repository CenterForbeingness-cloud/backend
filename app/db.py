"""
Shared Postgres connection pool for Supabase.

Reuses connections across requests instead of opening a new TCP+TLS session per query.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

from app.config import SUPABASE_DB_URL, logger

_pool = None


def init_db_pool() -> None:
    """Create the pool once at app startup (no-op if SUPABASE_DB_URL is unset)."""
    global _pool
    if _pool is not None or not SUPABASE_DB_URL:
        return
    try:
        from psycopg_pool import ConnectionPool

        # Supabase pooler (PgBouncer) does not support prepared statements across connections.
        _pool = ConnectionPool(
            conninfo=SUPABASE_DB_URL,
            min_size=1,
            max_size=8,
            kwargs={"autocommit": True, "prepare_threshold": None},
            open=True,
            timeout=10,
        )
        logger.info("Postgres connection pool ready (max_size=8)")
    except Exception as exc:
        logger.warning("Connection pool unavailable, using per-request connects: %s", exc)
        _pool = None


def close_db_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def db_connection() -> Generator:
    """Yield a connection from the pool, or a one-off connection if the pool failed."""
    if _pool is not None:
        with _pool.connection() as conn:
            yield conn
        return

    if not SUPABASE_DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not configured")

    import psycopg

    conn = psycopg.connect(
        SUPABASE_DB_URL,
        autocommit=True,
        connect_timeout=5,
        prepare_threshold=None,
    )
    try:
        yield conn
    finally:
        conn.close()


def get_pool() -> Optional[object]:
    return _pool
