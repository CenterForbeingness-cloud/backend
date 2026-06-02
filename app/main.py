import asyncio
import base64
import logging
import hashlib
import hmac
import json
import re
import time
from typing import Optional

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.chat_service import (
    iter_llm_sse,
    iter_scripted_sse,
    prepare_chat_context,
    process_chat_turn,
    produce_reply,
    finalize_chat_turn,
)
from app.auth import create_chat_token, get_chat_user, get_current_user
from app.config import (
    AUTH_ENFORCED,
    APP_TITLE,
    APP_VERSION,
    CHAT_TOKEN_ENFORCED,
    CHAT_TOKEN_SECRET,
    CORS_ORIGINS,
    DEFAULT_PROVIDER,
    FAIR_USE_LIMIT,
    RATE_LIMIT_ENABLED,
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from app.analytics import track, track_purchase_completed
from app.models import (
    AdminLoginRequest,
    AdminTokenResponse,
    AdminTOTPVerifyRequest,
    AnalyticsEventsRequest,
    BillingCheckoutRequest,
    BillingCheckoutResponse,
    BillingConfirmPaymentRequest,
    BillingConfirmPaymentResponse,
    BillingPaymentIntentRequest,
    BillingPaymentIntentResponse,
    ChatRequest,
    ChatResponse,
    ChatVoiceResponse,
    ChatTokenResponse,
    RetrievalHitResponse,
    CourseDetailResponse,
    CourseItem,
    CourseListResponse,
    CourseProgressResponse,
    CreateSessionRequest,
    BundlePurchaseEligibility,
    EntitlementResponse,
    LessonItem,
    SessionListResponse,
    SessionMessagesResponse,
    SessionResponse,
    UsageResponse,
    UserProfileResponse,
    UserProfileUpdateRequest,
    WeekItem,
    generate_session_id,
)
from app.course_progress import advance_day, get_progress
from app.db import close_db_pool, init_db_pool
from app.rag import build_context_retriever
from app.lesson_state import reset_lesson_state
from app.storage import SessionAccessError, build_chat_store
from app.entitlements import (
    BUNDLE_INCLUDED_COURSES,
    apply_purchase_grant,
    apply_purchase_revoke,
    assert_bundle_purchase_allowed,
    bundle_included_slugs,
    check_entitlement,
    evaluate_bundle_purchase_eligibility,
    get_user_entitlements,
    resolve_chat_plan,
)
from app.quotas import get_usage_info
from app.user_profile import (
    get_user_profile,
    profile_has_launch_memory,
    upsert_user_profile,
)
from app.admin_auth import admin_login, verify_totp as verify_admin_totp
from app.rate_limit import AUTH_LIMIT, BILLING_LIMIT, CHAT_LIMIT, SESSIONS_LIMIT, limiter
from app.voice import (
    assert_voice_enabled,
    check_voice_quota,
    record_voice_usage,
    synthesize_speech,
    transcribe_audio,
)

logger = logging.getLogger(__name__)

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS if CORS_ORIGINS else ["*"],
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

chat_store = build_chat_store()
context_retriever = build_context_retriever()


@app.on_event("startup")
def _startup_db_pool() -> None:
    if AUTH_ENFORCED and not CHAT_TOKEN_SECRET and CHAT_TOKEN_ENFORCED:
        raise RuntimeError(
            "CHAT_TOKEN_ENFORCED=true but no chat token secret is configured"
        )
    if AUTH_ENFORCED and not CORS_ORIGINS:
        logger.warning(
            "AUTH_ENFORCED=true but CORS_ORIGINS is empty; this allows wildcard CORS"
        )
    if AUTH_ENFORCED and not STRIPE_WEBHOOK_SECRET and STRIPE_SECRET_KEY:
        logger.warning(
            "STRIPE_SECRET_KEY is set but STRIPE_WEBHOOK_SECRET is missing; "
            "webhook signature verification is disabled"
        )
    if RATE_LIMIT_ENABLED:
        logger.info("IP rate limiting enabled")
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


def _profile_response(user_id: str) -> UserProfileResponse:
    profile = get_user_profile(user_id)
    if profile is None:
        return UserProfileResponse(user_id=user_id, has_launch_memory=False)
    return UserProfileResponse(
        user_id=profile.user_id,
        display_name=profile.display_name,
        primary_goal=profile.primary_goal,
        secondary_goal=profile.secondary_goal,
        current_focus=profile.current_focus,
        energy_level=profile.energy_level,
        motivation_type=profile.motivation_type,
        has_launch_memory=profile_has_launch_memory(profile),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


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


async def _extract_user_and_course_from_charge(
    event_obj: dict,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract user/course for charge-based webhook events.

    Stripe charge events may not always include metadata directly, so this
    falls back to retrieving metadata from the related payment_intent.
    Returns (user_id, course_slug, payment_intent_id).
    """
    user_id, course_slug = _extract_user_and_course(event_obj)
    payment_intent_id = str(event_obj.get("payment_intent") or "").strip() or None

    if user_id and course_slug:
        return user_id, course_slug, payment_intent_id
    if not payment_intent_id or not STRIPE_SECRET_KEY:
        return user_id, course_slug, payment_intent_id

    try:
        intent_resp = await _stripe_request("GET", f"/payment_intents/{payment_intent_id}")
        if intent_resp.status_code >= 400:
            logger.warning(
                "Could not fetch payment_intent metadata for charge event (status=%s id=%s)",
                intent_resp.status_code,
                payment_intent_id,
            )
            return user_id, course_slug, payment_intent_id
        intent_obj = intent_resp.json()
        fallback_user_id, fallback_course_slug = _extract_user_and_course(intent_obj)
        return (
            user_id or fallback_user_id,
            course_slug or fallback_course_slug,
            payment_intent_id,
        )
    except Exception as exc:
        logger.warning(
            "Failed payment_intent metadata lookup for charge event id=%s: %s",
            payment_intent_id,
            exc,
        )
        return user_id, course_slug, payment_intent_id


# Stripe receipt_email / customer_email require a domain with a TLD (e.g. user@example.com).
_STRIPE_BILLING_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email_for_stripe(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    cleaned = str(email).strip()
    if not _STRIPE_BILLING_EMAIL_RE.fullmatch(cleaned):
        return None
    return cleaned


def _raise_for_stripe_error(resp: httpx.Response, *, fallback: str) -> None:
    if resp.status_code < 400:
        return
    detail = fallback
    try:
        err = (resp.json().get("error") or {})
        if isinstance(err, dict) and err.get("message"):
            detail = str(err["message"])
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        pass
    status = 400 if resp.status_code < 500 else 502
    raise HTTPException(status_code=status, detail=detail)


def _resolve_checkout_course_slug(price_id: str) -> Optional[str]:
    """
    Resolve server-side price_id -> course_slug mapping from course catalog.
    """
    from app.courses import list_courses

    for course in list_courses():
        if str(course.get("price_id") or "").strip() == price_id:
            slug = str(course.get("course_slug") or "").strip()
            return slug or None
    return None


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
@CHAT_LIMIT
def chat(
    request: Request,
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    _user: Optional[dict] = Depends(get_chat_user),
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
@CHAT_LIMIT
def chat_stream(
    request: Request,
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    _user: Optional[dict] = Depends(get_chat_user),
) -> StreamingResponse:
    user_id = _current_user_id(_user)

    def event_generator():
        ctx = prepare_chat_context(
            req, user_id, chat_store, default_provider=DEFAULT_PROVIDER
        )
        reply, provider_used, scripted, _retrieval = produce_reply(
            ctx, context_retriever
        )
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


@app.post("/chat/voice", response_model=ChatVoiceResponse)
@CHAT_LIMIT
async def chat_voice(
    request: Request,
    background_tasks: BackgroundTasks,
    session_id: str = Form(...),
    audio: UploadFile = File(...),
    course_slug: Optional[str] = Form(default=None),
    provider: Optional[str] = Form(default=None),
    week_number: Optional[int] = Form(default=None),
    day_number: Optional[int] = Form(default=None),
    _user: Optional[dict] = Depends(get_chat_user),
) -> ChatVoiceResponse:
    # TODO(post-MVP): upload TTS bytes to object storage; return signed URL instead of base64.
    assert_voice_enabled()

    user_id = _current_user_id(_user)
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    mime = audio.content_type or "audio/webm"
    user_text, spoken_seconds = transcribe_audio(audio_bytes, mime_type=mime)

    if user_id:
        check_voice_quota(user_id, spoken_seconds)
        track(
            user_id,
            "voice_utterance",
            spoken_seconds=spoken_seconds,
            course_slug=course_slug,
        )

    ai_provider = provider if provider in ("openai", "claude") else None
    req = ChatRequest(
        session_id=session_id,
        message=user_text,
        provider=ai_provider,
        course_slug=course_slug,
        week_number=week_number,
        day_number=day_number,
        mode="voice",
    )

    result = process_chat_turn(
        req,
        user_id,
        chat_store,
        context_retriever,
        background_tasks,
        default_provider=DEFAULT_PROVIDER,
    )

    audio_mp3, audio_mime = synthesize_speech(result.reply, course_slug=course_slug)

    if user_id:
        record_voice_usage(user_id, spoken_seconds)
        background_tasks.add_task(
            track,
            user_id,
            "voice_session_turn",
            spoken_seconds=spoken_seconds,
            course_slug=course_slug,
            rag_hit=bool(result.retrieval and result.retrieval.rag_hit),
        )

    hits = []
    retrieval = result.retrieval
    if retrieval:
        hits = [RetrievalHitResponse(**h.to_dict()) for h in retrieval.retrievals]

    return ChatVoiceResponse(
        session_id=session_id,
        reply=result.reply,
        transcript_user=user_text,
        audio_base64=base64.b64encode(audio_mp3).decode("ascii"),
        audio_mime=audio_mime,
        provider_used=result.provider_used,
        memory_size=result.memory_size,
        day_number=result.day_number,
        spoken_seconds=spoken_seconds,
        rag_hit=bool(retrieval and retrieval.rag_hit),
        retrievals=hits,
    )


@app.post("/analytics/events")
@AUTH_LIMIT
def ingest_analytics_events(
    request: Request,
    body: AnalyticsEventsRequest,
    _user: Optional[dict] = Depends(get_current_user),
) -> dict:
    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    for item in body.events:
        track(user_id, item.event_name, **item.properties)
    return {"ok": True, "count": len(body.events)}


@app.post("/chat/token", response_model=ChatTokenResponse)
@AUTH_LIMIT
def mint_chat_token(
    request: Request,
    _user: Optional[dict] = Depends(get_current_user),
) -> ChatTokenResponse:
    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    plan = resolve_chat_plan(user_id)
    token, expires_in = create_chat_token(user_id=user_id, plan=plan)
    return ChatTokenResponse(token=token, expires_in=expires_in)


@app.delete("/memory/{session_id}")
@SESSIONS_LIMIT
def clear_memory(
    request: Request,
    session_id: str,
    _user: Optional[dict] = Depends(get_current_user),
) -> dict:
    user_id = _current_user_id(_user)
    try:
        chat_store.clear_session(session_id, user_id)
        reset_lesson_state(session_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"ok": True, "session_id": session_id}


@app.post("/sessions", response_model=SessionResponse)
@SESSIONS_LIMIT
def create_session(
    request: Request,
    req: CreateSessionRequest,
    _user: Optional[dict] = Depends(get_current_user),
) -> SessionResponse:
    session_id = req.session_id or generate_session_id()
    user_id = _current_user_id(_user)
    try:
        chat_store.ensure_session(session_id, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SessionResponse(session_id=session_id)


@app.get("/sessions", response_model=SessionListResponse)
@SESSIONS_LIMIT
def list_sessions(
    request: Request,
    limit: int = 50,
    _user: Optional[dict] = Depends(get_current_user),
) -> SessionListResponse:
    safe_limit = max(1, min(limit, 200))
    user_id = _current_user_id(_user)
    try:
        sessions = chat_store.list_sessions(safe_limit, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SessionListResponse(sessions=sessions)


@app.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
@SESSIONS_LIMIT
def get_session_messages(
    request: Request,
    session_id: str,
    limit: int = 50,
    _user: Optional[dict] = Depends(get_current_user),
) -> SessionMessagesResponse:
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

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Stripe webhook secret is not configured",
        )
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
    processing_errors: list[str] = []

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
                    track_purchase_completed(
                        user_id,
                        course_slug,
                        stripe_event_id=event_id,
                    )
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
            processing_errors.append("checkout.session.completed")

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
            processing_errors.append("payment_intent.succeeded")

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
            payment_intent_id = None
            session_id = None

            if event_type.startswith("checkout.session"):
                session_id = stripe_obj_id
            elif event_type.startswith("charge."):
                user_id, course_slug, payment_intent_id = await _extract_user_and_course_from_charge(event_obj)

            if user_id and course_slug:
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
            processing_errors.append(event_type)

    if processing_errors:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process webhook event(s): {', '.join(sorted(set(processing_errors)))}",
        )

    return {
        "ok": True,
        "received": True,
        "event_id": event_id,
        "event_type": event_type,
    }


@app.post("/billing/checkout", response_model=BillingCheckoutResponse)
@BILLING_LIMIT
async def billing_checkout(
    request: Request,
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
    user_email = _email_for_stripe((_user or {}).get("email"))

    success_url = _as_url(req.success_url, "https://example.com/checkout/success")
    cancel_url = _as_url(req.cancel_url, "https://example.com/checkout/cancel")

    resolved_course_slug = _resolve_checkout_course_slug(req.price_id)
    if req.course_slug and resolved_course_slug and req.course_slug != resolved_course_slug:
        raise HTTPException(
            status_code=400,
            detail="price_id does not match requested course_slug",
        )
    if req.course_slug and not resolved_course_slug:
        raise HTTPException(
            status_code=400,
            detail="price_id is not mapped to a server-side course",
        )

    metadata_course_slug = resolved_course_slug or req.course_slug

    if metadata_course_slug:
        try:
            assert_bundle_purchase_allowed(user_id, metadata_course_slug)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    form_data: dict[str, str] = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": req.price_id,
        "line_items[0][quantity]": str(req.quantity),
        "client_reference_id": user_id or "anonymous",
    }

    if metadata_course_slug:
        form_data["metadata[course_slug]"] = metadata_course_slug
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
        _raise_for_stripe_error(stripe_resp, fallback="Stripe checkout creation failed")

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
@BILLING_LIMIT
async def billing_payment_intent(
    request: Request,
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
    raw_email = (_user or {}).get("email")
    user_email = _email_for_stripe(raw_email)
    if raw_email and not user_email:
        logger.warning(
            "Skipping receipt_email for payment intent; account email is not Stripe-valid: %s",
            raw_email,
        )

    try:
        price_resp = await _stripe_request("GET", f"/prices/{req.price_id}")
    except httpx.HTTPError as exc:
        logger.exception("Stripe price lookup failed: %s", exc)
        raise HTTPException(status_code=502, detail="Stripe price lookup failed") from exc

    if price_resp.status_code >= 400:
        logger.error("Stripe price lookup error (%s): %s", price_resp.status_code, price_resp.text)
        _raise_for_stripe_error(price_resp, fallback="Could not load Stripe price")

    price_payload = price_resp.json()
    unit_amount = price_payload.get("unit_amount")
    currency = str(price_payload.get("currency", "usd")).lower()
    active = bool(price_payload.get("active", False))

    if not active or not isinstance(unit_amount, int) or unit_amount <= 0:
        raise HTTPException(status_code=400, detail="Selected Stripe price is invalid or inactive")

    resolved_course_slug = _resolve_checkout_course_slug(req.price_id)
    if req.course_slug and resolved_course_slug and req.course_slug != resolved_course_slug:
        raise HTTPException(
            status_code=400,
            detail="price_id does not match requested course_slug",
        )
    if req.course_slug and not resolved_course_slug:
        raise HTTPException(
            status_code=400,
            detail="price_id is not mapped to a server-side course",
        )

    amount = unit_amount * req.quantity
    metadata: dict[str, str] = {}
    metadata_course_slug = resolved_course_slug or req.course_slug
    if metadata_course_slug:
        try:
            assert_bundle_purchase_allowed(user_id, metadata_course_slug)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        metadata["course_slug"] = metadata_course_slug
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
        _raise_for_stripe_error(intent_resp, fallback="Stripe payment intent creation failed")

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


@app.post("/billing/confirm-payment", response_model=BillingConfirmPaymentResponse)
@BILLING_LIMIT
async def billing_confirm_payment(
    request: Request,
    req: BillingConfirmPaymentRequest,
    _user: Optional[dict] = Depends(get_current_user),
) -> BillingConfirmPaymentResponse:
    """Grant entitlements immediately after in-app PaymentSheet success (webhook backup)."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe is not configured")

    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    intent_data: dict = {}
    status = ""
    for attempt in range(6):
        try:
            intent_resp = await _stripe_request("GET", f"/payment_intents/{req.payment_intent_id}")
        except httpx.HTTPError as exc:
            logger.exception("Stripe payment intent lookup failed: %s", exc)
            raise HTTPException(status_code=502, detail="Stripe payment intent lookup failed") from exc

        if intent_resp.status_code >= 400:
            _raise_for_stripe_error(intent_resp, fallback="Could not load payment intent")

        intent_data = intent_resp.json()
        status = str(intent_data.get("status") or "").strip()
        if status == "succeeded":
            break
        if status in {"processing", "requires_confirmation", "requires_action"} and attempt < 5:
            await asyncio.sleep(0.4 + attempt * 0.4)
            continue
        break

    if status != "succeeded":
        raise HTTPException(status_code=400, detail="Payment is not completed yet")

    metadata_user_id, course_slug = _extract_user_and_course(intent_data)
    if not metadata_user_id or metadata_user_id != user_id:
        raise HTTPException(status_code=403, detail="Payment does not belong to this account")
    if not course_slug:
        raise HTTPException(status_code=400, detail="Payment is missing course metadata")

    payment_intent_id = str(intent_data.get("id") or req.payment_intent_id).strip()
    if not apply_purchase_grant(
        user_id,
        course_slug,
        stripe_event_id=f"client_confirm:{payment_intent_id}",
        stripe_event_type="payment_intent.succeeded.client_confirm",
        stripe_reference_id=payment_intent_id,
        stripe_payment_intent_id=payment_intent_id,
    ):
        raise HTTPException(status_code=500, detail="Could not grant course access")

    owned = get_user_entitlements(user_id)
    logger.info(
        "Confirmed in-app payment: user=%s course=%s intent=%s",
        user_id,
        course_slug,
        payment_intent_id,
    )
    return BillingConfirmPaymentResponse(course_slug=course_slug, owned_courses=owned)


def _course_item_from_dict(course: dict) -> CourseItem:
    slug = str(course.get("course_slug") or "").strip()
    included = list(bundle_included_slugs(slug)) if slug in BUNDLE_INCLUDED_COURSES else None
    payload = dict(course)
    if included:
        payload["bundle_included_slugs"] = included
    return CourseItem(**payload)


@app.get("/courses", response_model=CourseListResponse)
def list_courses_endpoint() -> CourseListResponse:
    from app.courses import list_courses

    return CourseListResponse(
        courses=[_course_item_from_dict(c) for c in list_courses()]
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
    bundle_eligibility = [
        BundlePurchaseEligibility(**evaluate_bundle_purchase_eligibility(user_id, slug))
        for slug in BUNDLE_INCLUDED_COURSES
    ]
    return EntitlementResponse(
        owned_courses=owned_courses,
        bundle_eligibility=bundle_eligibility,
    )


@app.get("/profile", response_model=UserProfileResponse)
def get_profile(_user: Optional[dict] = Depends(get_current_user)) -> UserProfileResponse:
    """Return the authenticated user's companion profile (thin memory)."""
    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return _profile_response(user_id)


@app.patch("/profile", response_model=UserProfileResponse)
def update_profile(
    body: UserProfileUpdateRequest,
    _user: Optional[dict] = Depends(get_current_user),
) -> UserProfileResponse:
    """Create or update companion profile fields."""
    user_id = _current_user_id(_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return _profile_response(user_id)

    try:
        upsert_user_profile(user_id, fields)
    except RuntimeError as exc:
        logger.error("Profile update failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Profile storage unavailable. Run sql/supabase_user_profile.sql.",
        ) from exc

    return _profile_response(user_id)


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
@AUTH_LIMIT
def admin_login_endpoint(request: Request, req: AdminLoginRequest) -> dict:
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
@AUTH_LIMIT
def admin_verify_totp_endpoint(request: Request, req: AdminTOTPVerifyRequest) -> AdminTokenResponse:
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
