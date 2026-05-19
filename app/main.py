import logging
import hashlib
import hmac
import json
import time
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from app.ai import generate_reply
from app.auth import get_current_user
from app.config import (
    APP_TITLE,
    APP_VERSION,
    CORS_ORIGINS,
    DEFAULT_PROVIDER,
    FAIR_USE_LIMIT,
    MAX_MEMORY_MESSAGES,
    RAG_TOP_K,
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
    ChatRequest,
    ChatResponse,
    CourseDetailResponse,
    CourseItem,
    CourseListResponse,
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
from app.rag import build_context_retriever, load_base_script
from app.storage import SessionAccessError, build_chat_store
from app.entitlements import check_entitlement, get_user_entitlements, grant_entitlement, record_purchase_event
from app.quotas import check_quota, increment_message_count, get_usage_info
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
_BASE_SCRIPT: str | None = load_base_script()


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


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, _user: Optional[dict] = Depends(get_current_user)) -> ChatResponse:
    provider = req.provider or DEFAULT_PROVIDER
    user_id = _current_user_id(_user)

    # Check entitlement if accessing a paid course
    if req.course_slug and user_id:
        if not check_entitlement(user_id, req.course_slug):
            logger.warning("Entitlement denied: user=%s course=%s", user_id, req.course_slug)
            raise HTTPException(
                status_code=403,
                detail="Course access required. Please upgrade.",
                headers={"X-Upgrade-Required": "true"},
            )

    # Check quota
    if user_id:
        if not check_quota(user_id, FAIR_USE_LIMIT):
            logger.warning("Quota exceeded: user=%s", user_id)
            raise HTTPException(
                status_code=429,
                detail=f"Message limit reached ({FAIR_USE_LIMIT} per 24h). Please try again tomorrow.",
            )

    try:
        chat_store.append_message(req.session_id, "user", req.message, user_id)
        history = chat_store.get_history(req.session_id, MAX_MEMORY_MESSAGES, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    retrieved_context = context_retriever.retrieve(
        req.message,
        top_k=RAG_TOP_K,
        course_slug=req.course_slug,
        week_number=req.week_number,
    )

    try:
        reply = generate_reply(req.message, history, provider, retrieved_context, base_script=_BASE_SCRIPT)
    except Exception as exc:
        logger.exception("generate_reply failed: %s", exc)
        raise HTTPException(status_code=500, detail="AI service error. Please try again.") from exc

    chat_store.append_message(req.session_id, "assistant", reply, user_id)
    memory_size = len(chat_store.get_history(req.session_id, MAX_MEMORY_MESSAGES, user_id))

    # Increment quota counter
    if user_id:
        increment_message_count(user_id)

    return ChatResponse(
        session_id=req.session_id,
        reply=reply,
        provider_used=provider,
        memory_size=memory_size,
    )


@app.delete("/memory/{session_id}")
def clear_memory(session_id: str, _user: Optional[dict] = Depends(get_current_user)) -> dict:
    user_id = _current_user_id(_user)
    try:
        chat_store.clear_session(session_id, user_id)
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

    # Process checkout.session.completed events
    if event_type == "checkout.session.completed":
        try:
            session_data = event.get("data", {}).get("object", {})
            session_id = session_data.get("id")
            client_ref = session_data.get("client_reference_id")
            metadata = session_data.get("metadata", {})
            course_slug = metadata.get("course_slug")

            if session_id and client_ref and course_slug:
                # Record the event (idempotent)
                record_purchase_event(
                    stripe_event_id=event_id,
                    stripe_event_type=event_type,
                    stripe_session_id=session_id,
                    user_id=client_ref,
                    course_slug=course_slug,
                )

                # Grant entitlement
                grant_entitlement(
                    user_id=client_ref,
                    course_slug=course_slug,
                    granted_by="stripe",
                )
                logger.info("Granted entitlement via webhook: user=%s course=%s", client_ref, course_slug)
            else:
                logger.warning("Incomplete checkout data: session=%s ref=%s course=%s", session_id, client_ref, course_slug)
        except Exception as exc:
            logger.exception("Failed to process checkout.session.completed: %s", exc)

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
