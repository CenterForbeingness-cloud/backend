from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

from app.main import app

_CHAT_SECRET = "phase5-chat-secret"


def _chat_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_chat_token(
    *,
    secret: str = _CHAT_SECRET,
    user_id: str = "user-phase5",
    expires_delta: timedelta = timedelta(minutes=5),
    scope: str = "chat",
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "scope": scope,
        "plan": "free",
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def test_unauthenticated_chat_rejected_when_enforcement_on() -> None:
    """Phase 5 item 1: no Bearer token → 401 when chat token enforcement is on."""
    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ):
        client = TestClient(app)
        response = client.post(
            "/chat",
            json={"session_id": "phase5-s1", "message": "hello"},
        )

    assert response.status_code == 401
    body = response.json()
    assert body.get("detail") == "Chat token required"


def test_invalid_chat_token_rejected_when_enforcement_on() -> None:
    """Phase 5 item 2: wrong-signed chat token → 401."""
    bad_token = _make_chat_token(secret="wrong-secret")

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ):
        client = TestClient(app)
        response = client.post(
            "/chat",
            headers=_chat_headers(bad_token),
            json={"session_id": "phase5-s2-invalid", "message": "hello"},
        )

    assert response.status_code == 401
    assert response.json().get("detail") == "Invalid or expired chat token"


def test_expired_chat_token_rejected_when_enforcement_on() -> None:
    """Phase 5 item 2: expired chat token → 401."""
    expired_token = _make_chat_token(expires_delta=timedelta(minutes=-5))

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ):
        client = TestClient(app)
        response = client.post(
            "/chat",
            headers=_chat_headers(expired_token),
            json={"session_id": "phase5-s2-expired", "message": "hello"},
        )

    assert response.status_code == 401
    assert response.json().get("detail") == "Invalid or expired chat token"


def test_authenticated_chat_allowed_when_token_valid() -> None:
    """Phase 5 item 3: valid chat token passes the gate and chat can succeed."""
    from app.chat_service import ChatTurnResult

    token = _make_chat_token(user_id="user-phase5-ok")
    fake_result = ChatTurnResult(
        reply="Welcome. Take one slow breath.",
        provider_used="openai",
        memory_size=1,
        day_number=None,
        scripted=False,
    )

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ), patch(
        "app.main.process_chat_turn", return_value=fake_result
    ) as mock_turn:
        client = TestClient(app)
        response = client.post(
            "/chat",
            headers=_chat_headers(token),
            json={"session_id": "phase5-s3", "message": "hello"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Welcome. Take one slow breath."
    assert body["session_id"] == "phase5-s3"
    mock_turn.assert_called_once()
    assert mock_turn.call_args.args[1] == "user-phase5-ok"


def test_user_without_entitlement_blocked_from_paid_course_chat() -> None:
    """Phase 5 item 4: authenticated user without course access → 403."""
    token = _make_chat_token(user_id="user-phase5-no-access")

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ), patch(
        "app.chat_service.check_quota", return_value=True
    ), patch(
        "app.chat_service.check_entitlement", return_value=False
    ) as mock_entitlement:
        client = TestClient(app)
        response = client.post(
            "/chat",
            headers=_chat_headers(token),
            json={
                "session_id": "phase5-s4",
                "message": "start the lesson",
                "course_slug": "week-zero-reset",
            },
        )

    assert response.status_code == 403
    detail = response.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("upgrade_required") is True
    assert detail.get("course_slug") == "week-zero-reset"
    assert "Course access required" in str(detail.get("message", ""))
    mock_entitlement.assert_called_once_with("user-phase5-no-access", "week-zero-reset")
    assert response.headers.get("X-Upgrade-Required") == "true"


def test_user_with_entitlement_allowed_for_paid_course_chat() -> None:
    """Phase 5 item 5: authenticated user with course access can chat."""
    from unittest.mock import MagicMock

    from app.rag import RetrievalResult

    token = _make_chat_token(user_id="user-phase5-entitled")
    store = MagicMock()
    store.get_history.return_value = []

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ), patch(
        "app.chat_service.check_quota", return_value=True
    ), patch(
        "app.chat_service.check_entitlement", return_value=True
    ) as mock_entitlement, patch(
        "app.main.chat_store", store
    ), patch(
        "app.chat_service.produce_reply",
        return_value=(
            "Good. Stay with the breath.",
            "openai",
            False,
            RetrievalResult(),
        ),
    ):
        client = TestClient(app)
        response = client.post(
            "/chat",
            headers=_chat_headers(token),
            json={
                "session_id": "phase5-s5",
                "message": "start the lesson",
                "course_slug": "week-zero-reset",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Good. Stay with the breath."
    assert body["session_id"] == "phase5-s5"
    mock_entitlement.assert_called_once_with("user-phase5-entitled", "week-zero-reset")

_WEBHOOK_SECRET = "whsec_phase5_test_secret"


def _checkout_completed_payload(
    *,
    event_id: str = "evt_phase5_6",
    user_id: str = "00000000-0000-0000-0000-000000000101",
    course_slug: str = "week-zero-reset",
    session_id: str = "cs_test_phase5_6",
) -> bytes:
    """Raw JSON body as Stripe sends it (bytes matter for signature)."""
    import json

    body = {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "client_reference_id": user_id,
                "metadata": {
                    "user_id": user_id,
                    "course_slug": course_slug,
                },
            }
        },
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _stripe_signature_header(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build Stripe-Signature: t=<unix>,v1=<hmac_sha256_hex> matching app.main._verify_stripe_signature."""
    import hashlib
    import hmac
    import time

    ts = int(time.time()) if timestamp is None else timestamp
    signed_payload = f"{ts}.{payload.decode('utf-8')}"
    digest = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={digest}"


def test_billing_webhook_rejects_missing_stripe_signature() -> None:
    """Phase 5 item 6: no Stripe-Signature header → 400, no grant."""
    payload = _checkout_completed_payload()

    with patch("app.main.STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET), patch(
        "app.main.apply_purchase_grant"
    ) as mock_grant:
        client = TestClient(app)
        response = client.post(
            "/billing/webhook",
            content=payload,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json().get("detail") == "Missing Stripe-Signature header"
    mock_grant.assert_not_called()


def test_billing_webhook_rejects_invalid_stripe_signature() -> None:
    """Phase 5 item 6: wrong HMAC → 400 Invalid Stripe signature, no grant.

    Payload is otherwise a valid checkout.session.completed with user/course
    metadata so a bug that skipped verification would try to grant.
    """
    payload = _checkout_completed_payload()
    bad_header = _stripe_signature_header(payload, "whsec_attacker_secret")

    with patch("app.main.STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET), patch(
        "app.main.apply_purchase_grant"
    ) as mock_grant:
        client = TestClient(app)
        response = client.post(
            "/billing/webhook",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": bad_header,
            },
        )

    assert response.status_code == 400
    assert response.json().get("detail") == "Invalid Stripe signature"
    mock_grant.assert_not_called()


def test_billing_webhook_rejects_tampered_body_with_old_signature() -> None:
    """Phase 5 item 6: signature for body A must not accept body B."""
    original = _checkout_completed_payload(user_id="00000000-0000-0000-0000-000000000101")
    good_header = _stripe_signature_header(original, _WEBHOOK_SECRET)
    tampered = _checkout_completed_payload(user_id="00000000-0000-0000-0000-000000000999")

    with patch("app.main.STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET), patch(
        "app.main.apply_purchase_grant"
    ) as mock_grant:
        client = TestClient(app)
        response = client.post(
            "/billing/webhook",
            content=tampered,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": good_header,
            },
        )

    assert response.status_code == 400
    assert response.json().get("detail") == "Invalid Stripe signature"
    mock_grant.assert_not_called()

def test_valid_billing_webhook_grants_access_once() -> None:
    """Phase 5 item 7: correctly signed checkout.session.completed grants once."""
    user_id = "00000000-0000-0000-0000-000000000201"
    course_slug = "week-zero-reset"
    event_id = "evt_phase5_7_once"
    session_id = "cs_phase5_7_once"
    payload = _checkout_completed_payload(
        event_id=event_id,
        user_id=user_id,
        course_slug=course_slug,
        session_id=session_id,
    )
    signature = _stripe_signature_header(payload, _WEBHOOK_SECRET)

    with patch("app.main.STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET), patch(
        "app.entitlements.record_purchase_event", return_value="new"
    ) as mock_event, patch(
        "app.entitlements.record_course_purchase", return_value=True
    ) as mock_purchase, patch(
        "app.entitlements.grant_entitlement", return_value=True
    ) as mock_grant, patch(
        "app.main.track_purchase_completed"
    ):
        client = TestClient(app)
        response = client.post(
            "/billing/webhook",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": signature,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body.get("ok") is True
    assert body.get("event_id") == event_id
    assert body.get("event_type") == "checkout.session.completed"

    mock_event.assert_called_once()
    assert mock_event.call_args.kwargs["stripe_event_id"] == event_id
    assert mock_event.call_args.kwargs["user_id"] == user_id
    assert mock_event.call_args.kwargs["course_slug"] == course_slug

    mock_purchase.assert_called_once()
    assert mock_purchase.call_args.args[0] == user_id
    assert mock_purchase.call_args.args[1] == course_slug
    assert mock_purchase.call_args.kwargs.get("stripe_session_id") == session_id

    mock_grant.assert_called_once_with(
        user_id=user_id,
        course_slug=course_slug,
        granted_by="stripe",
    )


def test_duplicate_billing_webhook_does_not_double_grant() -> None:
    """Phase 5 item 7: same Stripe event again skips purchase + entitlement side effects."""
    user_id = "00000000-0000-0000-0000-000000000202"
    course_slug = "week-zero-reset"
    event_id = "evt_phase5_7_dup"
    session_id = "cs_phase5_7_dup"
    payload = _checkout_completed_payload(
        event_id=event_id,
        user_id=user_id,
        course_slug=course_slug,
        session_id=session_id,
    )

    with patch("app.main.STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET), patch(
        "app.entitlements.record_purchase_event",
        side_effect=["new", "duplicate"],
    ) as mock_event, patch(
        "app.entitlements.record_course_purchase", return_value=True
    ) as mock_purchase, patch(
        "app.entitlements.grant_entitlement", return_value=True
    ) as mock_grant, patch(
        "app.main.track_purchase_completed"
    ):
        client = TestClient(app)

        first = client.post(
            "/billing/webhook",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": _stripe_signature_header(payload, _WEBHOOK_SECRET),
            },
        )
        second = client.post(
            "/billing/webhook",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": _stripe_signature_header(payload, _WEBHOOK_SECRET),
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json().get("ok") is True
    assert second.json().get("ok") is True
    assert mock_event.call_count == 2
    assert mock_purchase.call_count == 1
    assert mock_grant.call_count == 1
    mock_grant.assert_called_once_with(
        user_id=user_id,
        course_slug=course_slug,
        granted_by="stripe",
    )

def test_confirm_payment_rejects_wrong_metadata_user() -> None:
    """Phase 5 item 8: caller may not claim a PaymentIntent owned by another user."""
    from unittest.mock import AsyncMock, MagicMock

    from app.auth import get_current_user

    caller_id = "00000000-0000-0000-0000-000000000301"
    metadata_owner_id = "00000000-0000-0000-0000-000000000999"
    payment_intent_id = "pi_phase5_8_wrong_user"

    intent_payload = {
        "id": payment_intent_id,
        "status": "succeeded",
        "metadata": {
            "user_id": metadata_owner_id,
            "course_slug": "week-zero-reset",
        },
    }
    stripe_resp = MagicMock()
    stripe_resp.status_code = 200
    stripe_resp.json.return_value = intent_payload

    app.dependency_overrides[get_current_user] = lambda: {"sub": caller_id}
    try:
        with patch("app.main.STRIPE_SECRET_KEY", "sk_test_phase5_8"), patch(
            "app.main._stripe_request",
            new=AsyncMock(return_value=stripe_resp),
        ), patch("app.main.apply_purchase_grant") as mock_grant:
            client = TestClient(app)
            response = client.post(
                "/billing/confirm-payment",
                json={"payment_intent_id": payment_intent_id},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json().get("detail") == "Payment does not belong to this account"
    mock_grant.assert_not_called()


def test_confirm_payment_rejects_missing_metadata_user() -> None:
    """Phase 5 item 8: succeeded intent without user metadata cannot be claimed."""
    from unittest.mock import AsyncMock, MagicMock

    from app.auth import get_current_user

    caller_id = "00000000-0000-0000-0000-000000000302"
    payment_intent_id = "pi_phase5_8_missing_user"

    intent_payload = {
        "id": payment_intent_id,
        "status": "succeeded",
        "metadata": {
            "course_slug": "week-zero-reset",
        },
    }
    stripe_resp = MagicMock()
    stripe_resp.status_code = 200
    stripe_resp.json.return_value = intent_payload

    app.dependency_overrides[get_current_user] = lambda: {"sub": caller_id}
    try:
        with patch("app.main.STRIPE_SECRET_KEY", "sk_test_phase5_8"), patch(
            "app.main._stripe_request",
            new=AsyncMock(return_value=stripe_resp),
        ), patch("app.main.apply_purchase_grant") as mock_grant:
            client = TestClient(app)
            response = client.post(
                "/billing/confirm-payment",
                json={"payment_intent_id": payment_intent_id},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json().get("detail") == "Payment does not belong to this account"
    mock_grant.assert_not_called()


def test_billing_webhook_skips_grant_when_user_metadata_missing() -> None:
    """Phase 5 item 8: signed webhook with no user id must not grant."""
    import json

    payload = json.dumps(
        {
            "id": "evt_phase5_8_no_user",
            "object": "event",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_phase5_8_no_user",
                    "object": "checkout.session",
                    "metadata": {"course_slug": "week-zero-reset"},
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signature = _stripe_signature_header(payload, _WEBHOOK_SECRET)

    with patch("app.main.STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET), patch(
        "app.main.apply_purchase_grant"
    ) as mock_grant:
        client = TestClient(app)
        response = client.post(
            "/billing/webhook",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert response.json().get("ok") is True
    mock_grant.assert_not_called()

def test_chat_ai_failure_returns_sensible_503_not_fake_success() -> None:
    """Phase 5 item 9: provider failure to HTTP 503 with a clear message, no echo reply."""
    from unittest.mock import MagicMock

    from app.ai import AI_UNAVAILABLE_MESSAGE, AIUnavailableError

    token = _make_chat_token(user_id="user-phase5-ai-fail")
    store = MagicMock()
    store.get_history.return_value = []

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ), patch(
        "app.chat_service.check_quota", return_value=True
    ), patch(
        "app.main.chat_store", store
    ), patch(
        "app.chat_service.generate_reply",
        side_effect=AIUnavailableError(AI_UNAVAILABLE_MESSAGE),
    ):
        client = TestClient(app)
        response = client.post(
            "/chat",
            headers=_chat_headers(token),
            json={"session_id": "phase5-s9", "message": "hello guide"},
        )

    assert response.status_code == 503
    detail = response.json().get("detail")
    assert detail == AI_UNAVAILABLE_MESSAGE
    assert detail == "AI unavailable. Please try again."
    assert "[MVP fallback]" not in str(detail)
    assert "You said:" not in str(detail)
    assert "reply" not in response.json()


def test_chat_stream_ai_failure_emits_sensible_error_event() -> None:
    """Phase 5 item 9: stream path emits SSE error with the same clear message."""
    from unittest.mock import MagicMock

    from app.ai import AI_UNAVAILABLE_MESSAGE, AIUnavailableError

    token = _make_chat_token(user_id="user-phase5-ai-stream-fail")
    store = MagicMock()
    store.get_history.return_value = []

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ), patch(
        "app.chat_service.check_quota", return_value=True
    ), patch(
        "app.main.try_scripted_reply", return_value=None
    ), patch(
        "app.main.chat_store", store
    ), patch(
        "app.chat_service.generate_reply_stream",
        side_effect=AIUnavailableError(AI_UNAVAILABLE_MESSAGE),
    ):
        client = TestClient(app)
        response = client.post(
            "/chat/stream",
            headers=_chat_headers(token),
            json={"session_id": "phase5-s9-stream", "message": "hello guide"},
        )

    assert response.status_code == 200
    body = response.text
    assert '"type": "error"' in body or '"type":"error"' in body
    assert AI_UNAVAILABLE_MESSAGE in body
    assert "AI unavailable. Please try again." in body
    assert "[MVP fallback]" not in body
    assert "You said:" not in body


def test_chat_stream_performs_one_generation_for_normal_turn() -> None:
    """Phase 5 item 10: stream LLM path calls generate_reply_stream once, never generate_reply."""
    from unittest.mock import MagicMock

    token = _make_chat_token(user_id="user-phase5-stream-once")
    store = MagicMock()
    store.get_history.return_value = []

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ), patch(
        "app.chat_service.check_quota", return_value=True
    ), patch(
        "app.main.try_scripted_reply", return_value=None
    ), patch(
        "app.main.chat_store", store
    ), patch(
        "app.chat_service.generate_reply"
    ) as mock_generate, patch(
        "app.chat_service.generate_reply_stream",
        return_value=iter(["One ", "breath."]),
    ) as mock_stream:
        client = TestClient(app)
        response = client.post(
            "/chat/stream",
            headers=_chat_headers(token),
            json={"session_id": "phase5-s10", "message": "hi"},
        )

    assert response.status_code == 200
    body = response.text
    assert "One" in body
    assert '"type": "done"' in body or '"type":"done"' in body
    mock_generate.assert_not_called()
    mock_stream.assert_called_once()


# --- Phase 5 item 11: production storage failure stays explicit ---


def test_production_chat_store_init_failure_is_explicit() -> None:
    """Phase 5 item 11: Postgres init failure in production raises; no silent memory store."""
    import pytest
    from unittest.mock import MagicMock

    from app.storage import PostgresChatStore, build_chat_store

    fake = MagicMock(spec=PostgresChatStore)
    fake.init.side_effect = RuntimeError("connection refused")

    with patch("app.storage.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.production_gates.is_production", return_value=True
    ), patch("app.storage.PostgresChatStore", return_value=fake):
        with pytest.raises(RuntimeError, match="Refusing in-memory fallback"):
            build_chat_store()


def test_production_course_catalog_db_failure_is_explicit() -> None:
    """Phase 5 item 11: course catalog DB failure in production raises; no filesystem fallback."""
    import pytest

    from app.courses import list_courses

    with patch("app.courses.SUPABASE_DB_URL", "postgresql://example"), patch(
        "app.courses._allow_filesystem_catalog", return_value=False
    ), patch(
        "app.courses._list_courses_from_db",
        side_effect=RuntimeError("relation courses does not exist"),
    ):
        with pytest.raises(RuntimeError, match="does not exist"):
            list_courses()


def test_production_phase4_storage_gates_reject_in_memory_store() -> None:
    """Phase 5 item 11: startup gate refuses InMemoryChatStore in production."""
    import pytest

    from app.production_gates import assert_production_phase4_storage_gates
    from app.storage import InMemoryChatStore

    with patch("app.production_gates.is_production", return_value=True), patch(
        "app.production_gates.assert_production_course_catalog_from_database"
    ):
        with pytest.raises(RuntimeError, match="Postgres chat storage"):
            assert_production_phase4_storage_gates(InMemoryChatStore(10))


# --- Phase 5 item 12: daily practice entitlement + guide schedule ---


def test_daily_practice_without_entitlement_is_blocked() -> None:
    """Phase 5 item 12: daily practice still requires course entitlement."""
    token = _make_chat_token(user_id="user-phase5-practice-blocked")

    with patch("app.config.CHAT_TOKEN_ENFORCED", True), patch(
        "app.config.CHAT_TOKEN_SECRET", _CHAT_SECRET
    ), patch(
        "app.chat_service.check_quota", return_value=True
    ), patch(
        "app.chat_service.check_entitlement", return_value=False
    ) as mock_entitlement:
        client = TestClient(app)
        response = client.post(
            "/chat",
            headers=_chat_headers(token),
            json={
                "session_id": "phase5-s12-blocked",
                "message": "Start today's practice",
                "course_slug": "week-zero-reset",
                "daily_practice": True,
            },
        )

    assert response.status_code == 403
    detail = response.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("upgrade_required") is True
    assert detail.get("course_slug") == "week-zero-reset"
    mock_entitlement.assert_called_once_with(
        "user-phase5-practice-blocked", "week-zero-reset"
    )


def test_daily_practice_guide_path_loads_schedule_when_entitled() -> None:
    """Phase 5 item 12: entitled daily practice loads guide schedule context."""
    from unittest.mock import MagicMock

    from app.chat_service import prepare_chat_context, try_scripted_reply
    from app.models import ChatRequest

    store = MagicMock()
    store.get_history.return_value = []
    schedule_day = {
        "day_number": 3,
        "day_title": "Witness",
        "content": "themes for the day",
    }

    with patch("app.chat_service.check_quota", return_value=True), patch(
        "app.chat_service.check_entitlement", return_value=True
    ) as mock_entitlement, patch(
        "app.chat_service.resolve_schedule_day_number", return_value=3
    ) as mock_resolve, patch(
        "app.chat_service.get_schedule_day", return_value=schedule_day
    ) as mock_get_day, patch(
        "app.chat_service.build_schedule_context_block",
        return_value="[GUIDE SCHEDULE DAY 3]",
    ) as mock_build, patch(
        "app.chat_service.SCHEDULE_MODE", "guide"
    ):
        req = ChatRequest(
            session_id="phase5-s12-guide",
            message="I'm ready",
            course_slug="week-zero-reset",
            daily_practice=True,
        )
        ctx = prepare_chat_context(
            req, "user-phase5-practice-ok", store, default_provider="openai"
        )
        scripted = try_scripted_reply(ctx)

    mock_entitlement.assert_called_once_with(
        "user-phase5-practice-ok", "week-zero-reset"
    )
    mock_resolve.assert_called_once()
    mock_get_day.assert_called_once_with("week-zero-reset", 3)
    mock_build.assert_called_once_with(schedule_day, guide_mode=True)
    assert ctx.schedule_day_number == 3
    assert ctx.schedule_system_block == "[GUIDE SCHEDULE DAY 3]"
    assert scripted is None  # guide mode does not use the script engine
