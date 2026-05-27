"""Unit tests for entitlement idempotency helpers."""

from unittest.mock import MagicMock, patch

from app.entitlements import apply_purchase_grant, record_purchase_event


def test_record_purchase_event_returns_duplicate_on_unique_violation() -> None:
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("duplicate key value violates unique constraint")

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch("app.entitlements._ensure_entitlements_schema"), patch(
        "app.entitlements._get_db_connection"
    ) as mock_conn:
        mock_conn.return_value.__enter__.return_value = conn
        status = record_purchase_event(
            stripe_event_id="evt_123",
            stripe_event_type="checkout.session.completed",
            stripe_session_id="cs_123",
            user_id="00000000-0000-0000-0000-000000000001",
            course_slug="mindful-foundations",
        )

    assert status == "duplicate"


def test_apply_purchase_grant_skips_side_effects_on_duplicate_event() -> None:
    with patch("app.entitlements.record_purchase_event", return_value="duplicate") as mock_event, patch(
        "app.entitlements.record_course_purchase"
    ) as mock_purchase, patch("app.entitlements.grant_entitlement") as mock_grant:
        ok = apply_purchase_grant(
            "00000000-0000-0000-0000-000000000001",
            "mindful-foundations",
            stripe_event_id="evt_dup",
            stripe_event_type="checkout.session.completed",
            stripe_reference_id="cs_dup",
            stripe_session_id="cs_dup",
        )

    assert ok is True
    mock_event.assert_called_once()
    mock_purchase.assert_not_called()
    mock_grant.assert_not_called()
