import os
from collections import defaultdict, deque
from typing import Deque, Dict, List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

load_dotenv()

app = FastAPI(title="Sentient Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_MEMORY_MESSAGES = 8
memory: Dict[str, Deque[Dict[str, str]]] = defaultdict(
    lambda: deque(maxlen=MAX_MEMORY_MESSAGES)
)


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    provider: Optional[Literal["openai", "claude"]] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    provider_used: str
    memory_size: int


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "Sentient backend is running. Visit /docs for API docs."


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "sentient-backend"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    provider = req.provider or os.getenv("AI_PROVIDER", "openai").lower()

    # Store user message in short-term memory.
    memory[req.session_id].append({"role": "user", "content": req.message})
    history: List[Dict[str, str]] = list(memory[req.session_id])

    try:
        reply = generate_reply(req.message, history, provider)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Store assistant response.
    memory[req.session_id].append({"role": "assistant", "content": reply})

    return ChatResponse(
        session_id=req.session_id,
        reply=reply,
        provider_used=provider,
        memory_size=len(memory[req.session_id]),
    )


@app.delete("/memory/{session_id}")
def clear_memory(session_id: str) -> dict:
    memory.pop(session_id, None)
    return {"ok": True, "session_id": session_id}


def generate_reply(
    latest_message: str, history: List[Dict[str, str]], provider: str
) -> str:
    # Bare-bones guardrail to keep tone stable for MVP.
    system_prompt = (
        "You are a calm meditation assistant. Keep responses concise, safe, and on-brand."
    )

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return f"[MVP fallback] You said: {latest_message}"

        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        messages = [{"role": "system", "content": system_prompt}] + history
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.5,
        )
        return response.choices[0].message.content or ""

    if provider == "claude":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return f"[MVP fallback] You said: {latest_message}"

        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=300,
            system=system_prompt,
            messages=history,
        )
        text_blocks = [b.text for b in response.content if getattr(b, "type", "") == "text"]
        return "\n".join(text_blocks).strip() or ""

    raise ValueError("Unsupported provider. Use 'openai' or 'claude'.")
