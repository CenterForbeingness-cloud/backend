import logging
import hashlib
import hmac
import json
import time
from typing import Optional

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
from app.chat_service import (
    iter_llm_sse,
    iter_scripted_sse,
    prepare_chat_context,
    process_chat_turn,
    produce_reply,
    finalize_chat_turn,
)
from app.auth import get_current_user
from app.config import (
    APP_TITLE,
    APP_VERSION,
    CORS_ORIGINS,
    DEFAULT_PROVIDER,
    FAIR_USE_LIMIT,
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from app.models import (
    AdminLoginRequest,
    AdminTokenResponse,
    AdminTOTPVerifyRequest,
    BillingCheckoutRequest,
    BillingCheckoutResponse,
    BillingPaymentIntentRequest,
    BillingPaymentIntentResponse,
    ChatRequest,
    ChatResponse,
    CourseDetailResponse,
    CourseItem,
    CourseListResponse,
    CourseProgressResponse,
    CreateSessionRequest,
    EntitlementResponse,
    LessonItem,
    SessionListResponse,
    SessionMessagesResponse,
    SessionResponse,
    UsageResponse,
    WeekItem,
    generate_session_id,
)
from app.course_progress import advance_day, get_progress
from app.db import close_db_pool, init_db_pool
from app.rag import build_context_retriever
from app.lesson_state import reset_lesson_state
from app.storage import SessionAccessError, build_chat_store
from app.entitlements import (
    apply_purchase_grant,
    apply_purchase_revoke,
    check_entitlement,
    get_user_entitlements,
)
from app.quotas import get_usage_info
from app.admin_auth import admin_login, verify_totp as verify_admin_totp

logger = logging.getLogger(__name__)

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS if CORS_ORIGINS else ["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

chat_store = build_chat_store()
context_retriever = build_context_retriever()


@app.on_event("startup")
def _startup_db_pool() -> None:
    init_db_pool()


@app.on_event("shutdown")
def _shutdown_db_pool() -> None:
    close_db_pool()


def _verify_stripe_signature(payload: bytes, signature_header: str, secret: str, tolerance_seconds: int = 300) -> bool:
    parts = {}
    for chunk in signature_header.split(","):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parts.setdefault(key.strip(), []).append(value.strip())

    ts_values = parts.get("t")
    v1_values = parts.get("v1")
    if not ts_values or not v1_values:
        return False

    try:
        timestamp = int(ts_values[0])
    except ValueError:
        return False

    now = int(time.time())
    if abs(now - timestamp) > tolerance_seconds:
        return False

    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return any(hmac.compare_digest(expected, candidate) for candidate in v1_values)


def _current_user_id(user: Optional[dict]) -> str | None:
    if user is None:
        return None
    return user.get("sub")


def _as_url(value: Optional[str], fallback: str) -> str:
    if value is None:
        return fallback
    cleaned = value.strip()
    return cleaned or fallback


def _checkout_return_page(title: str, message: str) -> str:
        return f"""
<!doctype html>
<html>
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <meta http-equiv=\"refresh\" content=\"2;url=io.supabase.flutter://login-callback/\" />
    <title>{title}</title>
    <style>
        body {{
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;
            background: #111827;
            color: #f3f4f6;
            display: grid;
            place-items: center;
            min-height: 100vh;
            padding: 24px;
        }}
        .card {{
            max-width: 480px;
            width: 100%;
            background: #1f2937;
            border-radius: 12px;
            padding: 20px;
            box-sizing: border-box;
        }}
        a {{
            display: inline-block;
            margin-top: 12px;
            text-decoration: none;
            background: #e7d38a;
            color: #17333c;
            font-weight: 700;
            padding: 10px 14px;
            border-radius: 10px;
        }}
        p {{ line-height: 1.45; }}
    </style>
    <script>
        setTimeout(function () {{
            window.location.href = 'io.supabase.flutter://login-callback/';
        }}, 500);
    </script>
</head>
<body>
    <div class=\"card\">
        <h1>{title}</h1>
        <p>{message}</p>
        <p>Returning to the app automatically...</p>
        <a href=\"io.supabase.flutter://login-callback/\">Open Sentient</a>
    </div>
</body>
</html>
"""


def _extract_user_and_course(event_obj: dict) -> tuple[Optional[str], Optional[str]]:
    metadata = event_obj.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    course_slug = metadata.get("course_slug")
    user_id = (
        event_obj.get("client_reference_id")
        or metadata.get("user_id")
        or metadata.get("client_reference_id")
    )

    if user_id is not None:
        user_id = str(user_id).strip() or None
    if course_slug is not None:
        course_slug = str(course_slug).strip() or None

    return user_id, course_slug


async def _stripe_request(
    method: str,
    path: str,
    *,
    data: Optional[dict[str, str]] = None,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await client.request(
            method,
            f"https://api.stripe.com/v1{path}",
            data=data,
            headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"},
        )


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "Sentient backend is running. Visit /docs for API docs."


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "sentient-backend",
        "storage": chat_store.__class__.__name__,
    }


@app.get("/billing/return/success", response_class=HTMLResponse)
def billing_return_success() -> str:
    return _checkout_return_page(
        title="Payment complete",
        message="Your payment was successful. Tap below to return to Sentient.",
    )


@app.get("/billing/return/cancel", response_class=HTMLResponse)
def billing_return_cancel() -> str:
    return _checkout_return_page(
        title="Checkout canceled",
        message="No problem. You can return to Sentient and continue anytime.",
    )


@app.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    _user: Optional[dict] = Depends(get_current_user),
) -> ChatResponse:
    user_id = _current_user_id(_user)
    result = process_chat_turn(
        req,
        user_id,
        chat_store,
        context_retriever,
        background_tasks,
        default_provider=DEFAULT_PROVIDER,
    )
    return ChatResponse(
        session_id=req.session_id,
        reply=result.reply,
        provider_used=result.provider_used,
        memory_size=result.memory_size,
        day_number=result.day_number,
    )


@app.post("/chat/stream")
def chat_stream(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    _user: Optional[dict] = Depends(get_current_user),
) -> StreamingResponse:
    user_id = _current_user_id(_user)

    def event_generator():
        ctx = prepare_chat_context(
            req, user_id, chat_store, default_provider=DEFAULT_PROVIDER
        )
        reply, provider_used, scripted = produce_reply(ctx, context_retriever)
        if scripted:
            result = finalize_chat_turn(
                ctx,
                reply,
                chat_store,
                background_tasks,
                provider_used=provider_used,
                scripted=True,
            )
            yield from iter_scripted_sse(result, req.session_id)
        else:
            yield from iter_llm_sse(
                ctx, context_retriever, chat_store, background_tasks
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/memory/{session_id}")
def clear_memory(session_id: str, _user: Optional[dict] = Depends(get_current_user)) -> dict:
    user_id = _current_user_id(_user)
    try:
        chat_store.clear_session(session_id, user_id)
        reset_lesson_state(session_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"ok": True, "session_id": session_id}


@app.post("/sessions", response_model=SessionResponse)
def create_session(req: CreateSessionRequest, _user: Optional[dict] = Depends(get_current_user)) -> SessionResponse:
    session_id = req.session_id or generate_session_id()
    user_id = _current_user_id(_user)
    try:
        chat_store.ensure_session(session_id, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SessionResponse(session_id=session_id)


@app.get("/sessions", response_model=SessionListResponse)
def list_sessions(limit: int = 50, _user: Optional[dict] = Depends(get_current_user)) -> SessionListResponse:
    safe_limit = max(1, min(limit, 200))
    user_id = _current_user_id(_user)
    try:
        sessions = chat_store.list_sessions(safe_limit, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SessionListResponse(sessions=sessions)


@app.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
def get_session_messages(session_id: str, limit: int = 50, _user: Optional[dict] = Depends(get_current_user)) -> SessionMessagesResponse:
    safe_limit = max(1, min(limit, 200))
    user_id = _current_user_id(_user)
    try:
        messages = chat_store.list_messages(session_id, safe_limit, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SessionMessagesResponse(session_id=session_id, messages=messages)


@app.post("/billing/webhook")
async def billing_webhook(request: Request, stripe_signature: str | None = Header(default=None, alias="Stripe-Signature")) -> dict:
    payload = await request.body()

    if STRIPE_WEBHOOK_SECRET:
        if not stripe_signature:
            raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")
        if not _verify_stripe_signature(payload, stripe_signature, STRIPE_WEBHOOK_SECRET):
            raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    try:
        event = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc

    event_type = str(event.get("type", "unknown"))
    event_id = str(event.get("id", "unknown"))
    logger.info("Stripe webhook received: id=%s type=%s", event_id, event_type)

    # Process purchase grant events.
    if event_type == "checkout.session.completed":
        try:
            session_data = event.get("data", {}).get("object", {})
            session_id = session_data.get("id")
            user_id, course_slug = _extract_user_and_course(session_data)

            if session_id and user_id and course_slug:
                if apply_purchase_grant(
                    user_id,
                    course_slug,
                    stripe_event_id=event_id,
                    stripe_event_type=event_type,
                    stripe_reference_id=session_id,
                    stripe_session_id=session_id,
                ):
                    logger.info(
                        "Granted entitlement via webhook: user=%s course=%s",
                        user_id,
                        course_slug,
                    )
                else:
                    logger.error(
                        "Failed to grant entitlement via checkout webhook: user=%s course=%s",
                        user_id,
                        course_slug,
                    )
            else:
                logger.warning("Incomplete checkout data: session=%s user=%s course=%s", session_id, user_id, course_slug)
        except Exception as exc:
            logger.exception("Failed to process checkout.session.completed: %s", exc)

    if event_type == "payment_intent.succeeded":
        try:
            intent_data = event.get("data", {}).get("object", {})
            payment_intent_id = str(intent_data.get("id", "unknown"))
            user_id, course_slug = _extract_user_and_course(intent_data)

            if payment_intent_id and user_id and course_slug:
                if apply_purchase_grant(
                    user_id,
                    course_slug,
                    stripe_event_id=event_id,
                    stripe_event_type=event_type,
                    stripe_reference_id=payment_intent_id,
                    stripe_payment_intent_id=payment_intent_id,
                ):
                    logger.info(
                        "Granted entitlement via payment intent: user=%s course=%s",
                        user_id,
                        course_slug,
                    )
                else:
                    logger.error(
                        "Failed to grant entitlement via payment_intent webhook: user=%s course=%s",
                        user_id,
                        course_slug,
                    )
            else:
                logger.warning(
                    "Incomplete payment_intent data: intent=%s user=%s course=%s",
                    payment_intent_id,
                    user_id,
                    course_slug,
                )
        except Exception as exc:
            logger.exception("Failed to process payment_intent.succeeded: %s", exc)

    # Process purchase revoke/refund events.
    # These handlers require user/course metadata on the Stripe object.
    if event_type in {
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
        "charge.refunded",
        "charge.dispute.funds_withdrawn",
    }:
        try:
            event_obj = event.get("data", {}).get("object", {})
            stripe_obj_id = str(event_obj.get("id", "unknown"))
            user_id, course_slug = _extract_user_and_course(event_obj)

            if user_id and course_slug:
                payment_intent_id = None
                session_id = None
                if event_type.startswith("checkout.session"):
                    session_id = stripe_obj_id
                elif event_type.startswith("charge."):
                    payment_intent_id = str(event_obj.get("payment_intent") or "") or None

                if apply_purchase_revoke(
                    user_id,
                    course_slug,
                    stripe_event_id=event_id,
                    stripe_event_type=event_type,
                    stripe_reference_id=stripe_obj_id,
                    reason=f"stripe:{event_type}",
                    stripe_session_id=session_id,
                    stripe_payment_intent_id=payment_intent_id,
                ):
                    logger.info(
                        "Revoked entitlement via webhook: user=%s course=%s event=%s",
                        user_id,
                        course_slug,
                        event_type,
                    )
            else:
                logger.warning(
                    "Refund/revoke event missing user/course metadata: event=%s object_id=%s",
                    event_type,
                    stripe_obj_id,
                )
        except Exception as exc:
            logger.exception("Failed to process %s: %s", event_type, exc)

    return {
        "ok": True,
        "received": True,
        "event_id": event_id,
        "event_type": event_type,
    }


@app.post("/billing/checkout", response_model=BillingCheckoutResponse)
async def billing_checkout(
    req: BillingCheckoutRequest,
    _user: Optional[dict] = Depends(get_current_user),
) -> BillingCheckoutResponse:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe is not configured")
    if not STRIPE_SECRET_KEY.startswith("sk_"):
        raise HTTPException(
            status_code=503,
            detail="Stripe secret key is invalid. Set STRIPE_SECRET_KEY to an sk_test_ or sk_live_ key.",
        )

    user_id = _current_user_id(_user)
    user_email = (_user or {}).get("email")

    success_url = _as_url(req.success_url, "https://example.com/checkout/success")
    cancel_url = _as_url(req.cancel_url, "https://example.com/checkout/cancel")

    form_data: dict[str, str] = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": req.price_id,
        "line_items[0][quantity]": str(req.quantity),
        "client_reference_id": user_id or "anonymous",
    }

    if req.course_slug:
        form_data["metadata[course_slug]"] = req.course_slug
    if user_id:
        form_data["metadata[user_id]"] = user_id
    if user_email:
        form_data["customer_email"] = str(user_email)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            stripe_resp = await client.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=form_data,
                headers={
                    "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
                },
            )
    except httpx.HTTPError as exc:
        logger.exception("Stripe checkout request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Stripe checkout request failed") from exc

    if stripe_resp.status_code >= 400:
        logger.error("Stripe checkout error (%s): %s", stripe_resp.status_code, stripe_resp.text)
        raise HTTPException(status_code=502, detail="Stripe checkout creation failed")

    payload = stripe_resp.json()
    checkout_url = payload.get("url")
    session_id = payload.get("id")
    if not checkout_url or not session_id:
        raise HTTPException(status_code=502, detail="Stripe checkout response missing fields")

    return BillingCheckoutResponse(
        checkout_url=str(checkout_url),
        session_id=str(session_id),
        publishable_key=STRIPE_PUBLISHABLE_KEY,
    )


@app.post("/billing/payment-intent", response_model=BillingPaymentIntentResponse)
async def billing_payment_intent(
    req: BillingPaymentIntentRequest,
    _user: Optional[dict] = Depends(get_current_user),
) -> BillingPaymentIntentResponse:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe is not configured")
    if not STRIPE_SECRET_KEY.startswith("sk_"):
        raise HTTPException(
            status_code=503,
            detail="Stripe secret key is invalid. Set STRIPE_SECRET_KEY to an sk_test_ or sk_live_ key.",
        )

    user_id = _current_user_id(_user)
    user_email = (_user or {}).get("email")

    try:
        price_resp = await _stripe_request("GET", f"/prices/{req.price_id}")
    except httpx.HTTPError as exc:
        logger.exception("Stripe price lookup failed: %s", exc)
        raise HTTPException(status_code=502, detail="Stripe price lookup failed") from exc

    if price_resp.status_code >= 400:
        logger.error("Stripe price lookup error (%s): %s", price_resp.status_code, price_resp.text)
        raise HTTPException(status_code=502, detail="Could not load Stripe price")

    price_payload = price_resp.json()
    unit_amount = price_payload.get("unit_amount")
    currency = str(price_payload.get("currency", "usd")).lower()
    active = bool(price_payload.get("active", False))

    if not active or not isinstance(unit_amount, int) or unit_amount <= 0:
        raise HTTPException(status_code=400, detail="Selected Stripe price is invalid or inactive")

    amount = unit_amount * req.quantity
    metadata: dict[str, str] = {}
    if req.course_slug:
        metadata["course_slug"] = req.course_slug
    if user_id:
        metadata["user_id"] = user_id

    form_data: dict[str, str] = {
        "amount": str(amount),
        "currency": currency,
        "automatic_payment_methods[enabled]": "true",
    }

    if user_email:
        form_data["receipt_email"] = str(user_email)

    for key, value in metadata.items():
        form_data[f"metadata[{key}]"] = value

    try:
        intent_resp = await _stripe_request("POST", "/payment_intents", data=form_data)
    except httpx.HTTPError as exc:
        logger.exception("Stripe payment intent request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Stripe payment intent request failed") from exc

    if intent_resp.status_code >= 400:
        logger.error("Stripe payment intent error (%s): %s", intent_resp.status_code, intent_resp.text)
        raise HTTPException(status_code=502, detail="Stripe payment intent creation failed")

    intent_payload = intent_resp.json()
    payment_intent_id = intent_payload.get("id")
    client_secret = intent_payload.get("client_secret")

    if not payment_intent_id or not client_secret:
        raise HTTPException(status_code=502, detail="Stripe payment intent response missing fields")

    return BillingPaymentIntentResponse(
        payment_intent_id=str(payment_intent_id),
        client_secret=str(client_secret),
        publishable_key=STRIPE_PUBLISHABLE_KEY,
    )


@app.get("/courses", response_model=CourseListResponse)
def list_courses_endpoint() -> CourseListResponse:
    from app.courses import list_courses

    return CourseListResponse(
        courses=[CourseItem(**c) for c in list_courses()]
    )


@app.get("/courses/{course_slug}", response_model=CourseDetailResponse)
def get_course_endpoint(course_slug: str) -> CourseDetailResponse:
    from app.courses import get_course_detail

    detail = get_course_detail(course_slug)
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"Course '{course_slug}' not found."
        )

    weeks = [
        WeekItem(
            week_number=w["week_number"],
            title=w["title"],
            lessons=[LessonItem(**l) for l in w["lessons"]],
        )
        for w in detail["weeks"]
    ]
    return CourseDetailResponse(
        course_slug=detail["course_slug"],
        title=detail["title"],
        weeks=weeks,
    )


@app.get("/courses/{course_slug}/progress", response_model=CourseProgressResponse)
def get_course_progress_endpoint(
    course_slug: str,
    _user: Optional[dict] = Depends(get_current_user),
) -> CourseProgressResponse:
    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not check_entitlement(user_id, course_slug):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Course access required.",
                "upgrade_required": True,
                "course_slug": course_slug,
            },
        )

    progress = get_progress(user_id, course_slug)
    if progress is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule progress for course '{course_slug}'.",
        )

    return CourseProgressResponse(**progress)


@app.post("/courses/{course_slug}/progress/advance", response_model=CourseProgressResponse)
def advance_course_progress_endpoint(
    course_slug: str,
    _user: Optional[dict] = Depends(get_current_user),
) -> CourseProgressResponse:
    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not check_entitlement(user_id, course_slug):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Course access required.",
                "upgrade_required": True,
                "course_slug": course_slug,
            },
        )

    progress = advance_day(user_id, course_slug)
    if progress is None:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot advance progress for course '{course_slug}'.",
        )

    return CourseProgressResponse(**progress)


@app.get("/entitlements", response_model=EntitlementResponse)
def get_entitlements(_user: Optional[dict] = Depends(get_current_user)) -> EntitlementResponse:
    """Return list of owned courses for the authenticated user."""
    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    owned_courses = get_user_entitlements(user_id)
    return EntitlementResponse(owned_courses=owned_courses)


@app.get("/usage", response_model=UsageResponse)
def get_usage(_user: Optional[dict] = Depends(get_current_user)) -> UsageResponse:
    """Return current message usage and quota info for the authenticated user."""
    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    usage = get_usage_info(user_id, FAIR_USE_LIMIT)
    return UsageResponse(
        messages_today=usage["messages_today"],
        limit=usage["limit"],
        reset_at=usage["reset_at"],
    )


@app.post("/admin/auth/login", response_model=dict)
def admin_login_endpoint(req: AdminLoginRequest) -> dict:
    """
    Admin login endpoint. Returns session_token for 2FA verification.
    
    Next step: POST /admin/auth/verify-totp with email + totp_code.
    """
    session_token, error = admin_login(req.email, req.password)
    if error or not session_token:
        logger.warning("Admin login failed: %s", req.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {
        "ok": True,
        "session_token": session_token,
        "next_step": "POST /admin/auth/verify-totp with totp_code",
    }


@app.post("/admin/auth/verify-totp", response_model=AdminTokenResponse)
def admin_verify_totp_endpoint(req: AdminTOTPVerifyRequest) -> AdminTokenResponse:
    """
    Verify TOTP code and return admin JWT token.
    
    Admin can then use admin_token for admin-only endpoints.
    """
    admin_token, error = verify_admin_totp(req.email, req.totp_code)
    if error or not admin_token:
        logger.warning("Admin TOTP verification failed: %s", req.email)
        raise HTTPException(status_code=401, detail="Invalid authenticator code")

    # Extract role from token (decode without verification for display)
    import jwt
    try:
        payload = jwt.decode(admin_token, options={"verify_signature": False})
        role = payload.get("admin_role", "viewer")
    except Exception:
        role = "viewer"

    return AdminTokenResponse(
        admin_token=admin_token,
        admin_email=req.email,
        role=role,
    )
