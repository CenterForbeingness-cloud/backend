from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    provider: Optional[Literal["openai", "claude"]] = None
    course_slug: Optional[str] = None
    week_number: Optional[int] = None
    day_number: Optional[int] = Field(default=None, ge=1)


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


class LessonItem(BaseModel):
    lesson_number: int
    title: str
    filename: str


class WeekItem(BaseModel):
    week_number: int
    title: str
    lessons: list[LessonItem]


class CourseItem(BaseModel):
    course_slug: str
    title: str
    description: str
    week_count: int
    price_id: Optional[str] = None
    unit_amount_cents: Optional[int] = None
    currency: Optional[str] = None


class CourseListResponse(BaseModel):
    courses: list[CourseItem]


class CourseDetailResponse(BaseModel):
    course_slug: str
    title: str
    weeks: list[WeekItem]


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


class BillingPaymentIntentRequest(BaseModel):
    price_id: str = Field(..., min_length=1)
    quantity: int = Field(default=1, ge=1, le=10)
    course_slug: Optional[str] = None


class BillingPaymentIntentResponse(BaseModel):
    payment_intent_id: str
    client_secret: str
    publishable_key: Optional[str] = None


class EntitlementResponse(BaseModel):
    owned_courses: list[str]


class UsageResponse(BaseModel):
    messages_today: int
    limit: int
    reset_at: datetime


class AdminLoginRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)


class AdminTOTPVerifyRequest(BaseModel):
    email: str = Field(..., min_length=1)
    totp_code: str = Field(..., pattern="^[0-9]{6}$")


class AdminTokenResponse(BaseModel):
    admin_token: str
    admin_email: str
    role: str


def generate_session_id() -> str:
    return f"session-{uuid4().hex}"
