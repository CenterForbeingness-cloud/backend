from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from app.ai import generate_reply
from app.config import APP_TITLE, APP_VERSION, DEFAULT_PROVIDER, MAX_MEMORY_MESSAGES
from app.models import ChatRequest, ChatResponse
from app.storage import build_chat_store

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

chat_store = build_chat_store()


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
def chat(req: ChatRequest) -> ChatResponse:
    provider = req.provider or DEFAULT_PROVIDER

    chat_store.append_message(req.session_id, "user", req.message)
    history = chat_store.get_history(req.session_id, MAX_MEMORY_MESSAGES)

    try:
        reply = generate_reply(req.message, history, provider)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    chat_store.append_message(req.session_id, "assistant", reply)
    memory_size = len(chat_store.get_history(req.session_id, MAX_MEMORY_MESSAGES))

    return ChatResponse(
        session_id=req.session_id,
        reply=reply,
        provider_used=provider,
        memory_size=memory_size,
    )


@app.delete("/memory/{session_id}")
def clear_memory(session_id: str) -> dict:
    chat_store.clear_session(session_id)
    return {"ok": True, "session_id": session_id}
