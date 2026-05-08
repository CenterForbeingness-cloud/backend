import logging
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from app.ai import generate_reply
from app.auth import get_current_user
from app.config import APP_TITLE, APP_VERSION, CORS_ORIGINS, DEFAULT_PROVIDER, MAX_MEMORY_MESSAGES, RAG_TOP_K
from app.models import (
    ChatRequest,
    ChatResponse,
    CreateSessionRequest,
    SessionMessagesResponse,
    SessionResponse,
    generate_session_id,
)
from app.rag import build_context_retriever
from app.storage import SessionAccessError, build_chat_store

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


def _current_user_id(user: Optional[dict]) -> str | None:
    if user is None:
        return None
    return user.get("sub")


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

    try:
        chat_store.append_message(req.session_id, "user", req.message, user_id)
        history = chat_store.get_history(req.session_id, MAX_MEMORY_MESSAGES, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    retrieved_context = context_retriever.retrieve(req.message, top_k=RAG_TOP_K)

    try:
        reply = generate_reply(req.message, history, provider, retrieved_context)
    except Exception as exc:
        logger.exception("generate_reply failed: %s", exc)
        raise HTTPException(status_code=500, detail="AI service error. Please try again.") from exc

    chat_store.append_message(req.session_id, "assistant", reply, user_id)
    memory_size = len(chat_store.get_history(req.session_id, MAX_MEMORY_MESSAGES, user_id))

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


@app.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
def get_session_messages(session_id: str, limit: int = 50, _user: Optional[dict] = Depends(get_current_user)) -> SessionMessagesResponse:
    safe_limit = max(1, min(limit, 200))
    user_id = _current_user_id(_user)
    try:
        messages = chat_store.list_messages(session_id, safe_limit, user_id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SessionMessagesResponse(session_id=session_id, messages=messages)
