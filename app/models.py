from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    provider: Optional[Literal["openai", "claude"]] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    provider_used: str
    memory_size: int


class CreateSessionRequest(BaseModel):
    session_id: Optional[str] = None


class SessionResponse(BaseModel):
    session_id: str


class MessageItem(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[MessageItem]


class SessionSummaryItem(BaseModel):
    session_id: str
    updated_at: datetime
    message_count: int
    last_message_preview: str


class SessionListResponse(BaseModel):
    sessions: list[SessionSummaryItem]


class BillingCheckoutRequest(BaseModel):
    price_id: str = Field(..., min_length=1)
    quantity: int = Field(default=1, ge=1, le=10)
    course_slug: Optional[str] = None
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class BillingCheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str
    publishable_key: Optional[str] = None


def generate_session_id() -> str:
    return f"session-{uuid4().hex}"
