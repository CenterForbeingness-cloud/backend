"""Hardening Phase 4: production refuses silent storage and catalog fallbacks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.production_gates import (
    assert_production_course_catalog_from_database,
    assert_production_persistent_chat_store,
    assert_production_phase4_storage_gates,
)
from app.storage import InMemoryChatStore, PostgresChatStore, build_chat_store


def test_build_chat_store_memory_allowed_in_development_without_db() -> None:
    with patch("app.storage.SUPABASE_DB_URL", None), patch(
        "app.production_gates.is_production", return_value=False
    ):
        store = build_chat_store()
        assert isinstance(store, InMemoryChatStore)


def test_build_chat_store_refuses_memory_in_production_without_db() -> None:
    with patch("app.storage.SUPABASE_DB_URL", None), patch(
        "app.production_gates.is_production", return_value=True
    ):
        with pytest.raises(RuntimeError, match="Refusing in-memory chat store"):
            build_chat_store()


def test_build_chat_store_refuses_memory_when_postgres_init_fails_in_production() -> None:
    fake = MagicMock(spec=PostgresChatStore)
    fake.init.side_effect = RuntimeError("boom")

    with patch("app.storage.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.production_gates.is_production", return_value=True
    ), patch("app.storage.PostgresChatStore", return_value=fake):
        with pytest.raises(RuntimeError, match="Refusing in-memory fallback"):
            build_chat_store()


def test_build_chat_store_memory_fallback_when_postgres_init_fails_in_development() -> None:
    fake = MagicMock(spec=PostgresChatStore)
    fake.init.side_effect = RuntimeError("boom")

    with patch("app.storage.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.production_gates.is_production", return_value=False
    ), patch("app.storage.PostgresChatStore", return_value=fake):
        store = build_chat_store()
        assert isinstance(store, InMemoryChatStore)


def test_assert_production_persistent_chat_store_rejects_memory() -> None:
    with patch("app.production_gates.is_production", return_value=True):
        with pytest.raises(RuntimeError, match="Postgres chat storage"):
            assert_production_persistent_chat_store(InMemoryChatStore(10))


def test_assert_production_persistent_chat_store_accepts_postgres() -> None:
    store = PostgresChatStore.__new__(PostgresChatStore)
    with patch("app.production_gates.is_production", return_value=True):
        assert_production_persistent_chat_store(store)


def test_assert_production_course_catalog_fails_when_query_fails() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.SUPABASE_DB_URL", "postgresql://example"
    ), patch(
        "app.courses._list_courses_from_db", side_effect=RuntimeError("missing table")
    ):
        with pytest.raises(RuntimeError, match="course catalog"):
            assert_production_course_catalog_from_database()


def test_assert_production_course_catalog_passes_on_empty_list() -> None:
    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.config.SUPABASE_DB_URL", "postgresql://example"
    ), patch("app.courses._list_courses_from_db", return_value=[]):
        assert_production_course_catalog_from_database()


def test_list_courses_raises_in_production_when_db_fails() -> None:
    from app.courses import list_courses

    with patch("app.courses.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.courses._allow_filesystem_catalog", return_value=False
    ), patch(
        "app.courses._list_courses_from_db", side_effect=RuntimeError("db down")
    ):
        with pytest.raises(RuntimeError, match="db down"):
            list_courses()


def test_get_course_detail_does_not_use_filesystem_in_production_when_missing() -> None:
    from app.courses import get_course_detail

    with patch("app.courses.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.courses._allow_filesystem_catalog", return_value=False
    ), patch("app.courses._course_detail_from_db", return_value=None), patch(
        "app.courses._courses_dir"
    ) as mock_dir:
        assert get_course_detail("missing-course") is None
        mock_dir.assert_not_called()


def test_phase4_gates_noop_in_development() -> None:
    with patch("app.production_gates.is_production", return_value=False):
        assert_production_phase4_storage_gates(InMemoryChatStore(10))
