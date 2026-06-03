from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    provider: Optional[Literal["openai", "claude"]] = None
    course_slug: Optional[str] = None
    week_number: Optional[int] = None
    day_number: Optional[int] = Field(default=None, ge=1)
    mode: Literal["text", "voice"] = "text"


class RetrievalHitResponse(BaseModel):
    id: str
    score: float
    course_slug: Optional[str] = None
    source_type: str = "text"
    lesson: Optional[str] = None
    week_number: Optional[int] = None


class ChatVoiceResponse(BaseModel):
    session_id: str
    reply: str
    transcript_user: str
    audio_base64: str
    audio_mime: str = "audio/mpeg"
    provider_used: str
    memory_size: int
    day_number: Optional[int] = None
    spoken_seconds: float
    rag_hit: bool
    retrievals: list[RetrievalHitResponse] = Field(default_factory=list)


class AnalyticsEventItem(BaseModel):
    event_name: str = Field(..., min_length=1, max_length=120)
    properties: dict = Field(default_factory=dict)


class AnalyticsEventsRequest(BaseModel):
    events: list[AnalyticsEventItem] = Field(..., min_length=1, max_length=20)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    provider_used: str
    memory_size: int
    day_number: Optional[int] = None


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
    bundle_included_slugs: Optional[list[str]] = None


class BundlePurchaseEligibility(BaseModel):
    bundle_slug: str
    eligible: bool
    included_course_slugs: list[str]
    owned_included_slugs: list[str]
    message: Optional[str] = None


class CourseListResponse(BaseModel):
    courses: list[CourseItem]


class CourseDetailResponse(BaseModel):
    course_slug: str
    title: str
    weeks: list[WeekItem]


class CourseProgressResponse(BaseModel):
    course_slug: str
    current_day_number: int
    max_day_number: Optional[int] = None
    day_title: Optional[str] = None
    duration_minutes: Optional[int] = None
    welcome_message: Optional[str] = None


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


class BillingConfirmPaymentRequest(BaseModel):
    payment_intent_id: str = Field(..., min_length=1)


class BillingConfirmPaymentResponse(BaseModel):
    course_slug: str
    owned_courses: list[str]


class EntitlementResponse(BaseModel):
    owned_courses: list[str]
    bundle_eligibility: list[BundlePurchaseEligibility] = Field(default_factory=list)


class UsageResponse(BaseModel):
    messages_today: int
    limit: int
    reset_at: datetime


class UserProfileResponse(BaseModel):
    user_id: str
    display_name: Optional[str] = None
    primary_goal: Optional[str] = None
    secondary_goal: Optional[str] = None
    current_focus: Optional[str] = None
    energy_level: Optional[str] = None
    motivation_type: Optional[str] = None
    has_launch_memory: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UserProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    primary_goal: Optional[str] = Field(default=None, max_length=500)
    secondary_goal: Optional[str] = Field(default=None, max_length=500)
    current_focus: Optional[str] = Field(default=None, max_length=500)
    energy_level: Optional[str] = Field(default=None, max_length=80)
    motivation_type: Optional[str] = Field(default=None, max_length=80)


class ChatTokenResponse(BaseModel):
    token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


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


class AdminGrantEntitlementRequest(BaseModel):
    user_id: str = Field(..., min_length=36, max_length=36)
    course_slug: str = Field(..., min_length=1, max_length=80)
    note: Optional[str] = Field(default=None, max_length=500)


class AdminRevokeEntitlementRequest(BaseModel):
    user_id: str = Field(..., min_length=36, max_length=36)
    course_slug: str = Field(..., min_length=1, max_length=80)
    reason: Optional[str] = Field(default=None, max_length=200)


class AdminEntitlementMutationResponse(BaseModel):
    ok: bool
    granted_slugs: list[str] = Field(default_factory=list)
    revoked_slugs: list[str] = Field(default_factory=list)


class AdminUserSummary(BaseModel):
    user_id: str
    email: Optional[str] = None
    owned_courses: list[str] = Field(default_factory=list)
    messages_today: int = 0
    chat_plan: str = "free"


class AdminUsersResponse(BaseModel):
    users: list[AdminUserSummary]


class AdminMeResponse(BaseModel):
    admin_id: str
    email: str
    role: str


class AdminUserProfileSnippet(BaseModel):
    display_name: Optional[str] = None
    primary_goal: Optional[str] = None
    secondary_goal: Optional[str] = None
    current_focus: Optional[str] = None
    energy_level: Optional[str] = None
    motivation_type: Optional[str] = None
    updated_at: Optional[datetime] = None


class AdminEntitlementRow(BaseModel):
    course_slug: str
    granted_at: Optional[datetime] = None
    granted_by: str = "unknown"
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None


class AdminPurchaseRow(BaseModel):
    id: int
    course_slug: str
    purchase_source: str
    purchased_at: datetime
    refunded_at: Optional[datetime] = None
    stripe_session_id: Optional[str] = None
    stripe_payment_intent_id: Optional[str] = None


class AdminAnalyticsEventRow(BaseModel):
    event_name: str
    created_at: datetime
    properties: Optional[dict] = None


class AdminUsageSnippet(BaseModel):
    messages_today: int = 0
    limit: int = 0
    reset_at: Optional[datetime] = None


class AdminUserDetailResponse(BaseModel):
    user_id: str
    email: Optional[str] = None
    profile: Optional[AdminUserProfileSnippet] = None
    entitlements: list[AdminEntitlementRow] = Field(default_factory=list)
    purchases: list[AdminPurchaseRow] = Field(default_factory=list)
    usage: AdminUsageSnippet
    chat_plan: str = "free"
    recent_events: list[AdminAnalyticsEventRow] = Field(default_factory=list)


class AdminAuditLogEntry(BaseModel):
    id: int
    admin_id: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Optional[dict] = None
    created_at: datetime


class AdminAuditLogResponse(BaseModel):
    logs: list[AdminAuditLogEntry]
    total: int


class AdminStaffMember(BaseModel):
    admin_id: str
    email: str
    role: str
    is_active: bool = True
    totp_enabled: bool = False
    last_login: Optional[datetime] = None


class AdminStaffListResponse(BaseModel):
    staff: list[AdminStaffMember]


class AdminCourseItem(BaseModel):
    course_slug: str
    title: str
    price_id: Optional[str] = None
    is_published: bool = True
    bundle_included_slugs: list[str] = Field(default_factory=list)


class AdminCoursesResponse(BaseModel):
    courses: list[AdminCourseItem]


class AdminCreateStaffRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field(default="viewer", pattern="^(owner|editor|viewer)$")


class AdminCreateStaffResponse(BaseModel):
    ok: bool
    admin_id: str
    email: str
    role: str
    totp_secret: str
    totp_provisioning_uri: str


class AdminUpdateStaffRequest(BaseModel):
    role: Optional[str] = Field(default=None, pattern="^(owner|editor|viewer)$")
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "AdminUpdateStaffRequest":
        if self.role is None and self.is_active is None:
            raise ValueError("At least one of role or is_active is required")
        return self


class AdminUpdateStaffResponse(BaseModel):
    ok: bool
    admin_id: str
    email: str
    role: str
    is_active: bool
    previous_role: str
    previous_is_active: bool


# Backward-compatible aliases
class AdminUpdateRoleRequest(AdminUpdateStaffRequest):
    pass


class AdminUpdateRoleResponse(AdminUpdateStaffResponse):
    pass


def generate_session_id() -> str:
    return f"session-{uuid4().hex}"
