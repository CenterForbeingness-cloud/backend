"""
Shared /chat and /chat/stream logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterator, List, Optional

from fastapi import BackgroundTasks, HTTPException

from app.ai import generate_reply, generate_reply_stream
from app.config import (
    FAIR_USE_LIMIT,
    MAX_MEMORY_MESSAGES,
    RAG_TOP_K,
    SCHEDULE_HISTORY_MESSAGES,
    SCHEDULE_SCRIPT_ENGINE,
    logger,
)
from app.course_progress import resolve_schedule_day_number, touch_course_activity
from app.daily_schedule import build_schedule_system_block, get_schedule_day
from app.entitlements import check_entitlement
from app.lesson_script import cached_lesson_beats, coach_reply
from app.lesson_state import get_beat_index, set_beat_index
from app.models import ChatRequest
from app.quotas import check_quota, increment_message_count
from app.rag import load_base_script
from app.storage import SessionAccessError
from app.user_profile import load_profile_prompt_block

_BASE_SCRIPT = load_base_script()


@dataclass
class ChatContext:
    req: ChatRequest
    user_id: Optional[str]
    history: List[dict]
    history_limit: int
    schedule_day_number: Optional[int]
    schedule_day: Optional[dict]
    schedule_system_block: Optional[str]
    provider: str


@dataclass
class ChatTurnResult:
    reply: str
    provider_used: str
    memory_size: int
    day_number: Optional[int]
    scripted: bool


def prepare_chat_context(
    req: ChatRequest,
    user_id: Optional[str],
    chat_store,
    *,
    default_provider: str,
) -> ChatContext:
    provider = req.provider or default_provider

    if user_id and not check_quota(user_id, FAIR_USE_LIMIT):
        logger.warning("Quota exceeded: user=%s", user_id)
        raise HTTPException(
            status_code=429,
            detail=f"Message limit reached ({FAIR_USE_LIMIT} per 24h). Please try again tomorrow.",
        )

    if req.course_slug:
        if not user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Course access required. Please upgrade.",
                    "upgrade_required": True,
                    "course_slug": req.course_slug,
                },
                headers={"X-Upgrade-Required": "true"},
            )
        if not check_entitlement(user_id, req.course_slug):
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Course access required. Please upgrade.",
                    "upgrade_required": True,
                    "course_slug": req.course_slug,
                },
                headers={"X-Upgrade-Required": "true"},
            )

    schedule_day_number = resolve_schedule_day_number(
        user_id, req.course_slug, req.day_number
    )

    schedule_day = None
    schedule_system_block = None
    if req.course_slug and schedule_day_number:
        schedule_day = get_schedule_day(req.course_slug, schedule_day_number)
        if schedule_day:
            schedule_system_block = build_schedule_system_block(schedule_day)

    history_limit = (
        SCHEDULE_HISTORY_MESSAGES if schedule_system_block else MAX_MEMORY_MESSAGES
    )

    try:
        chat_store.append_message(req.session_id, "user", req.message, user_id)
        history = chat_store.get_history(req.session_id, history_limit, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return ChatContext(
        req=req,
        user_id=user_id,
        history=history,
        history_limit=history_limit,
        schedule_day_number=schedule_day_number,
        schedule_day=schedule_day,
        schedule_system_block=schedule_system_block,
        provider=provider,
    )


def _scripted_reply(ctx: ChatContext) -> Optional[str]:
    if not SCHEDULE_SCRIPT_ENGINE:
        logger.info("script engine off (SCHEDULE_SCRIPT_ENGINE=false)")
        return None
    if not ctx.req.course_slug:
        logger.debug("script skip: no course_slug on request")
        return None
    if not ctx.schedule_day_number:
        logger.warning(
            "script skip: no schedule day for course=%s", ctx.req.course_slug
        )
        return None
    if not ctx.schedule_day:
        logger.warning(
            "script skip: no schedule row course=%s day=%s",
            ctx.req.course_slug,
            ctx.schedule_day_number,
        )
        return None

    beats = list(cached_lesson_beats(ctx.schedule_day["content"]))
    if len(beats) < 2:
        logger.warning(
            "script skip: only %s beats parsed for course=%s day=%s",
            len(beats),
            ctx.req.course_slug,
            ctx.schedule_day_number,
        )
        return None

    beat_index = get_beat_index(
        ctx.req.session_id, ctx.req.course_slug, ctx.schedule_day_number
    )
    reply, new_index = coach_reply(beats, beat_index, ctx.req.message)
    set_beat_index(
        ctx.req.session_id,
        ctx.req.course_slug,
        ctx.schedule_day_number,
        new_index,
    )
    logger.info(
        "script coach course=%s day=%s beat %s->%s",
        ctx.req.course_slug,
        ctx.schedule_day_number,
        beat_index,
        new_index,
    )
    return reply


def produce_reply(
    ctx: ChatContext,
    context_retriever,
) -> tuple[str, str, bool]:
    """Return (reply, provider_used, scripted)."""
    scripted = _scripted_reply(ctx)
    if scripted is not None:
        return scripted, "script", True

    if ctx.req.course_slug and ctx.schedule_day:
        logger.warning(
            "falling back to LLM for course=%s day=%s — fix schedule import or beats",
            ctx.req.course_slug,
            ctx.schedule_day_number,
        )

    retrieved_context: List[str] = []
    use_rag = ctx.schedule_system_block is None or ctx.req.week_number is not None
    if use_rag:
        retrieved_context.extend(
            context_retriever.retrieve(
                ctx.req.message,
                top_k=RAG_TOP_K,
                course_slug=ctx.req.course_slug,
                week_number=ctx.req.week_number,
            )
        )

    profile_block = load_profile_prompt_block(ctx.user_id)

    try:
        reply = generate_reply(
            ctx.req.message,
            ctx.history,
            ctx.provider,
            retrieved_context,
            base_script=_BASE_SCRIPT,
            schedule_system_block=ctx.schedule_system_block,
            profile_system_block=profile_block,
        )
    except Exception as exc:
        logger.exception("generate_reply failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="AI service error. Please try again."
        ) from exc

    return reply, ctx.provider, False


def finalize_chat_turn(
    ctx: ChatContext,
    reply: str,
    chat_store,
    background_tasks: BackgroundTasks,
    *,
    provider_used: str,
    scripted: bool,
) -> ChatTurnResult:
    chat_store.append_message(ctx.req.session_id, "assistant", reply, ctx.user_id)
    memory_size = min(len(ctx.history) + 1, ctx.history_limit)

    if ctx.user_id:
        background_tasks.add_task(increment_message_count, ctx.user_id)
        if ctx.req.course_slug:
            background_tasks.add_task(
                touch_course_activity, ctx.user_id, ctx.req.course_slug
            )

    return ChatTurnResult(
        reply=reply,
        provider_used=provider_used,
        memory_size=memory_size,
        day_number=ctx.schedule_day_number,
        scripted=scripted,
    )


def process_chat_turn(
    req: ChatRequest,
    user_id: Optional[str],
    chat_store,
    context_retriever,
    background_tasks: BackgroundTasks,
    *,
    default_provider: str,
) -> ChatTurnResult:
    ctx = prepare_chat_context(req, user_id, chat_store, default_provider=default_provider)
    reply, provider_used, scripted = produce_reply(ctx, context_retriever)
    return finalize_chat_turn(
        ctx, reply, chat_store, background_tasks, provider_used=provider_used, scripted=scripted
    )


def iter_scripted_sse(result: ChatTurnResult, session_id: str) -> Iterator[str]:
    """SSE events for instant scripted replies."""
    text = result.reply
    parts = [p.strip() for p in text.replace("\n", " ").split(". ") if p.strip()]
    if not parts:
        parts = [text]
    for part in parts:
        chunk = part if part.endswith((".", "!", "?")) else f"{part}."
        payload = json.dumps({"type": "token", "content": chunk + " "})
        yield f"data: {payload}\n\n"

    done = json.dumps(
        {
            "type": "done",
            "reply": result.reply,
            "session_id": session_id,
            "provider_used": result.provider_used,
            "memory_size": result.memory_size,
            "day_number": result.day_number,
        }
    )
    yield f"data: {done}\n\n"


def iter_llm_sse(
    ctx: ChatContext,
    context_retriever,
    chat_store,
    background_tasks: BackgroundTasks,
) -> Iterator[str]:
    """Stream OpenAI tokens when not on the script engine."""
    retrieved_context: List[str] = []
    use_rag = ctx.schedule_system_block is None or ctx.req.week_number is not None
    if use_rag:
        retrieved_context.extend(
            context_retriever.retrieve(
                ctx.req.message,
                top_k=RAG_TOP_K,
                course_slug=ctx.req.course_slug,
                week_number=ctx.req.week_number,
            )
        )

    profile_block = load_profile_prompt_block(ctx.user_id)

    parts: List[str] = []
    try:
        for token in generate_reply_stream(
            ctx.req.message,
            ctx.history,
            ctx.provider,
            retrieved_context,
            base_script=_BASE_SCRIPT,
            schedule_system_block=ctx.schedule_system_block,
            profile_system_block=profile_block,
        ):
            parts.append(token)
            payload = json.dumps({"type": "token", "content": token})
            yield f"data: {payload}\n\n"
    except Exception as exc:
        logger.exception("generate_reply_stream failed: %s", exc)
        payload = json.dumps(
            {"type": "error", "message": "AI service error. Please try again."}
        )
        yield f"data: {payload}\n\n"
        return

    reply = "".join(parts)
    finalize_chat_turn(
        ctx,
        reply,
        chat_store,
        background_tasks,
        provider_used=ctx.provider,
        scripted=False,
    )
    done = json.dumps(
        {
            "type": "done",
            "reply": reply,
            "session_id": ctx.req.session_id,
            "provider_used": ctx.provider,
            "memory_size": min(len(ctx.history) + 1, ctx.history_limit),
            "day_number": ctx.schedule_day_number,
        }
    )
    yield f"data: {done}\n\n"
