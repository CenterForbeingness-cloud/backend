"""Stripe price_id -> course_slug resolution (env + catalog)."""

from unittest.mock import patch

from app.main import _resolve_checkout_course_slug


def test_resolve_from_stripe_price_env_map():
    with patch(
        "app.config.STRIPE_PRICE_BY_COURSE_SLUG",
        {"week-zero-reset": "price_test_wzr"},
    ):
        assert _resolve_checkout_course_slug("price_test_wzr") == "week-zero-reset"


def test_resolve_unknown_price_returns_none():
    with patch("app.config.STRIPE_PRICE_BY_COURSE_SLUG", {}):
        with patch(
            "app.courses.list_courses",
            return_value=[{"course_slug": "other", "price_id": "price_other"}],
        ):
            assert _resolve_checkout_course_slug("price_unknown") is None
