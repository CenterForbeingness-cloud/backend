from unittest.mock import MagicMock

from app.rate_limit import get_client_ip


def test_get_client_ip_prefers_x_forwarded_for() -> None:
    request = MagicMock()
    request.headers = {"X-Forwarded-For": "203.0.113.1, 70.41.3.18"}
    request.client = MagicMock(host="10.0.0.1")
    assert get_client_ip(request) == "203.0.113.1"


def test_get_client_ip_falls_back_to_client_host() -> None:
    request = MagicMock()
    request.headers = {}
    request.client = MagicMock(host="192.168.1.5")
    assert get_client_ip(request) == "192.168.1.5"
