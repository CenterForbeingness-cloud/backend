"""Marketing traffic beacon helpers."""

from app.marketing_traffic import normalize_page_path, normalize_session_id


def test_normalize_page_path_strips_query() -> None:
    assert normalize_page_path("/?utm=1") == "/"
    assert normalize_page_path("about") == "/about"


def test_normalize_session_id_rejects_short() -> None:
    assert normalize_session_id("abc") == ""


def test_normalize_session_id_accepts_uuid_like() -> None:
    assert normalize_session_id("abcd1234efgh5678ij") == "abcd1234efgh5678ij"
