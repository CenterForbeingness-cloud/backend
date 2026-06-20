"""Tests for Phase 2 memory prompt block."""

from unittest.mock import MagicMock, patch

from app.memory import format_memory_system_block, record_memory_event


@patch("app.memory._ensure_memory_schema", return_value=True)
@patch("app.memory._get_db_connection")
def test_format_memory_system_block_empty(mock_conn, _schema):
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.return_value = conn

    assert format_memory_system_block("user-1") is None


@patch("app.memory.SUPABASE_DB_URL", "postgresql://test")
@patch("app.memory._ensure_memory_schema", return_value=True)
@patch("app.memory._get_db_connection")
def test_format_memory_system_block_with_events(mock_conn, _schema):
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [],  # facts
        [],  # goals
        [("Completed day 1 in week-zero-reset",)],  # events
    ]
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.return_value = conn

    block = format_memory_system_block("user-1")
    assert block is not None
    assert "Completed day 1" in block
    assert "Recent events" in block


@patch("app.memory.SUPABASE_DB_URL", None)
def test_record_memory_event_no_db():
    record_memory_event("user-1", "test", "summary")
