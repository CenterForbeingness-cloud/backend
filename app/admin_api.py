"""
admin_api.py — Phase 5 admin routes (minimal v1).

Protected endpoints require Authorization: Bearer <admin_token> from
POST /admin/auth/verify-totp. See docs/ADMIN_V1_SPEC.md.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.admin_auth import (
    delete_admin_user,
    list_admin_staff,
    update_admin_staff,
    verify_admin_token,
)
from app.admin_invite import create_admin_invite
from app.admin_password_reset import request_password_reset_by_admin_id
from app.email_service import (
    build_admin_invite_url,
    build_admin_password_reset_url,
    send_admin_invite_email,
    send_admin_password_reset_email,
)
from app.config import FAIR_USE_LIMIT, SUPABASE_DB_URL, logger
from app.user_profile import get_user_profile
from app.daily_schedule import validate_course_slug
from app.entitlements import (
    entitlement_grants_for_product,
    get_user_entitlements,
    grant_entitlement,
    resolve_chat_plan,
    revoke_entitlement,
)
from app.admin_courses import (
    create_admin_course,
    get_admin_course_detail,
    list_all_admin_courses,
    replace_admin_course_schedule,
    replace_admin_course_weeks,
    update_admin_course,
    upsert_admin_course_product,
    upsert_admin_course_voice,
)
from app.admin_ops import get_admin_analytics_summary, get_admin_schedule_health
from app.admin_waitlist import (
    export_waitlist_csv,
    get_waitlist_stats,
    list_waitlist_signups,
    waitlist_table_available,
)
from app.models import (
    AdminAnalyticsEventRow,
    AdminAnalyticsSummaryResponse,
    AdminAuditLogEntry,
    AdminAuditLogResponse,
    AdminCourseDetailResponse,
    AdminCourseItem,
    AdminCoursesResponse,
    AdminCreateCourseRequest,
    AdminReplaceScheduleRequest,
    AdminReplaceWeeksRequest,
    AdminScheduleReplaceResponse,
    AdminUpdateCourseRequest,
    AdminUpsertProductRequest,
    AdminUpsertVoiceRequest,
    AdminInviteStaffRequest,
    AdminInviteStaffResponse,
    AdminEntitlementMutationResponse,
    AdminEntitlementRow,
    AdminGrantEntitlementRequest,
    AdminMeResponse,
    AdminPurchaseRow,
    AdminRevokeEntitlementRequest,
    AdminScheduleHealthResponse,
    AdminSendPasswordResetResponse,
    AdminStaffListResponse,
    AdminStaffMember,
    AdminDeleteStaffResponse,
    AdminUpdateStaffRequest,
    AdminUpdateStaffResponse,
    AdminUsageSnippet,
    AdminUserDetailResponse,
    AdminUserProfileSnippet,
    AdminUserSummary,
    AdminUsersResponse,
    AdminWaitlistListResponse,
    AdminWaitlistStatsResponse,
)
from app.quotas import get_usage_info

_admin_bearer = HTTPBearer(auto_error=True)

router = APIRouter(prefix="/admin", tags=["admin"])

_MUTATION_ROLES = frozenset({"owner", "editor"})
_OWNER_ROLE = "owner"

_ADMIN_UI_DIR = Path(__file__).resolve().parent.parent / "static" / "admin"

_AUDIT_FILTER_ACTIONS = frozenset({
    "GRANT_ENTITLEMENT",
    "REVOKE_ENTITLEMENT",
    "ADMIN_LOGIN",
    "ADMIN_ROLE_CHANGE",
    "ADMIN_USER_CREATE",
    "ADMIN_USER_DEACTIVATE",
    "ADMIN_USER_ACTIVATE",
    "ADMIN_USER_DELETE",
    "CREATE_COURSE",
    "UPDATE_COURSE",
    "PUBLISH_COURSE",
})


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Security(_admin_bearer),
) -> dict:
    """A1: FastAPI dependency — valid admin JWT required."""
    payload = verify_admin_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def _require_mutation_role(admin: dict) -> None:
    role = str(admin.get("admin_role") or "viewer")
    if role not in _MUTATION_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role cannot modify entitlements",
        )


def _require_owner_role(admin: dict) -> None:
    role = str(admin.get("admin_role") or "viewer")
    if role != _OWNER_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners can manage admin roles",
        )


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def write_admin_audit_log(
    *,
    admin_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    details: Optional[dict] = None,
    request: Optional[Request] = None,
    http_status_code: Optional[int] = None,
) -> None:
    """A2: Append-only admin audit trail."""
    if not SUPABASE_DB_URL:
        logger.warning("Skipping admin audit log: SUPABASE_DB_URL not set")
        return

    http_method = request.method if request else None
    http_path = str(request.url.path) if request else None
    client_ip = _client_ip(request) if request else None
    details_json = json.dumps(details) if details else None

    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.admin_audit_log (
                    admin_id,
                    action,
                    resource_type,
                    resource_id,
                    details,
                    http_method,
                    http_path,
                    http_status_code,
                    client_ip
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                """,
                (
                    admin_id,
                    action,
                    resource_type,
                    resource_id,
                    details_json,
                    http_method,
                    http_path,
                    http_status_code,
                    client_ip,
                ),
            )
    except Exception as exc:
        logger.exception("write_admin_audit_log failed action=%s: %s", action, exc)


def _normalize_user_id(user_id: str) -> str:
    try:
        return str(uuid.UUID(user_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user_id") from exc


def admin_grant_entitlements(
    user_id: str,
    course_slug: str,
    *,
    note: Optional[str] = None,
) -> list[str]:
    """Grant product slug and bundle children (idempotent)."""
    try:
        validate_course_slug(course_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    granted: list[str] = []
    for slug in entitlement_grants_for_product(course_slug):
        if grant_entitlement(user_id=user_id, course_slug=slug, granted_by="admin"):
            granted.append(slug)
        else:
            logger.warning(
                "admin grant failed user=%s course=%s note=%s",
                user_id,
                slug,
                note,
            )
    return granted


def admin_revoke_entitlements(
    user_id: str,
    course_slug: str,
    *,
    reason: str = "admin_revoke",
) -> list[str]:
    """Revoke product slug and bundle children."""
    try:
        validate_course_slug(course_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    revoked: list[str] = []
    for slug in entitlement_grants_for_product(course_slug):
        if revoke_entitlement(user_id=user_id, course_slug=slug, reason=reason):
            revoked.append(slug)
    return revoked


def search_admin_users(*, query: str, limit: int) -> list[AdminUserSummary]:
    """Search auth.users by email prefix."""
    if not SUPABASE_DB_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    q = query.strip().lower()
    if not q:
        return []

    limit = max(1, min(limit, 100))
    pattern = f"{q}%"

    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, email
                FROM auth.users
                WHERE lower(email) LIKE %s
                ORDER BY email
                LIMIT %s
                """,
                (pattern, limit),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.exception("search_admin_users failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="User search failed",
        ) from exc

    summaries: list[AdminUserSummary] = []
    for user_id, email in rows:
        owned = get_user_entitlements(user_id)
        usage = get_usage_info(user_id, FAIR_USE_LIMIT)
        summaries.append(
            AdminUserSummary(
                user_id=user_id,
                email=email,
                owned_courses=owned,
                messages_today=int(usage.get("messages_today") or 0),
                chat_plan=resolve_chat_plan(user_id),
            )
        )
    return summaries


def _fetch_entitlement_rows(cur: Any, uid: str) -> list:
    """Active/revoked entitlement rows (entitlements schema)."""
    cur.execute(
        """
        SELECT
            course_slug,
            granted_at,
            granted_by,
            revoked_at,
            revoked_by,
            revoke_reason
        FROM public.user_entitlements
        WHERE user_id = %s
        ORDER BY granted_at DESC NULLS LAST
        """,
        (uid,),
    )
    return cur.fetchall()


def _fetch_purchase_rows(cur: Any, uid: str) -> list:
    """Best-effort purchases: entitlements schema, then billing schema."""
    try:
        cur.execute(
            """
            SELECT
                id,
                course_slug,
                purchase_source,
                purchased_at,
                refunded_at,
                stripe_session_id,
                stripe_payment_intent_id
            FROM public.course_purchases
            WHERE user_id = %s
            ORDER BY purchased_at DESC NULLS LAST
            LIMIT 50
            """,
            (uid,),
        )
        return cur.fetchall()
    except Exception as exc:
        logger.warning("admin purchases (entitlements cols) skipped: %s", exc)

    try:
        cur.execute(
            """
            SELECT
                id,
                course_slug,
                provider,
                COALESCE(purchased_at, created_at),
                refunded_at,
                provider_checkout_session_id,
                provider_payment_intent_id
            FROM public.course_purchases
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (uid,),
        )
        return cur.fetchall()
    except Exception as exc:
        logger.warning("admin purchases (billing cols) skipped: %s", exc)
        return []


def _fetch_analytics_rows(cur: Any, uid: str) -> list:
    """Recent analytics events; empty if table missing."""
    try:
        cur.execute(
            """
            SELECT event_name, created_at, properties
            FROM public.analytics_events
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 25
            """,
            (uid,),
        )
        return cur.fetchall()
    except Exception as exc:
        logger.warning("admin analytics skipped user=%s: %s", uid, exc)
        return []


def get_admin_user_detail(user_id: str) -> AdminUserDetailResponse:
    """Load one app user for the admin detail panel."""
    if not SUPABASE_DB_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    uid = _normalize_user_id(user_id)

    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT email FROM auth.users WHERE id = %s",
                (uid,),
            )
            user_row = cur.fetchone()
            if user_row is None:
                raise HTTPException(status_code=404, detail="User not found")
            email = user_row[0]

            try:
                entitlement_rows = _fetch_entitlement_rows(cur, uid)
            except Exception as exc:
                logger.exception("get_admin_user_detail entitlements user=%s: %s", uid, exc)
                raise HTTPException(
                    status_code=502,
                    detail="Entitlements read failed",
                ) from exc

            purchase_rows = _fetch_purchase_rows(cur, uid)
            event_rows = _fetch_analytics_rows(cur, uid)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get_admin_user_detail failed user=%s: %s", uid, exc)
        raise HTTPException(status_code=502, detail="User detail read failed") from exc

    profile = get_user_profile(uid)
    profile_snippet: Optional[AdminUserProfileSnippet] = None
    if profile:
        profile_snippet = AdminUserProfileSnippet(
            display_name=profile.display_name,
            primary_goal=profile.primary_goal,
            secondary_goal=profile.secondary_goal,
            current_focus=profile.current_focus,
            energy_level=profile.energy_level,
            motivation_type=profile.motivation_type,
            updated_at=profile.updated_at,
        )

    entitlements = [
        AdminEntitlementRow(
            course_slug=str(row[0]),
            granted_at=row[1],
            granted_by=str(row[2] or "unknown"),
            revoked_at=row[3],
            revoked_by=row[4],
            revoke_reason=row[5],
        )
        for row in entitlement_rows
    ]
    purchases = [
        AdminPurchaseRow(
            id=int(row[0]),
            course_slug=str(row[1]),
            purchase_source=str(row[2] or "unknown"),
            purchased_at=row[3],
            refunded_at=row[4],
            stripe_session_id=row[5],
            stripe_payment_intent_id=row[6],
        )
        for row in purchase_rows
    ]
    recent_events: list[AdminAnalyticsEventRow] = []
    for row in event_rows:
        props = row[2]
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except json.JSONDecodeError:
                props = None
        recent_events.append(
            AdminAnalyticsEventRow(
                event_name=str(row[0]),
                created_at=row[1],
                properties=props if isinstance(props, dict) else None,
            )
        )

    usage_raw = get_usage_info(uid, FAIR_USE_LIMIT)
    usage = AdminUsageSnippet(
        messages_today=int(usage_raw.get("messages_today") or 0),
        limit=int(usage_raw.get("limit") or FAIR_USE_LIMIT),
        reset_at=usage_raw.get("reset_at"),
    )

    return AdminUserDetailResponse(
        user_id=uid,
        email=email,
        profile=profile_snippet,
        entitlements=entitlements,
        purchases=purchases,
        usage=usage,
        chat_plan=resolve_chat_plan(uid),
        recent_events=recent_events,
    )


def list_admin_courses() -> AdminCoursesResponse:
    """All catalog rows (draft + published) plus env-only slugs."""
    return AdminCoursesResponse(courses=list_all_admin_courses())


def list_admin_audit_log(
    *,
    limit: int,
    offset: int,
    action: Optional[str] = None,
) -> AdminAuditLogResponse:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    if action is not None:
        action = action.strip()
        if action and action not in _AUDIT_FILTER_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action filter. Allowed: {', '.join(sorted(_AUDIT_FILTER_ACTIONS))}",
            )

    try:
        from app.db import db_connection

        where_sql = ""
        params: list[Any] = []
        if action:
            where_sql = " WHERE action = %s"
            params.append(action)

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM public.admin_audit_log{where_sql}",
                params,
            )
            total_row = cur.fetchone()
            total = int(total_row[0]) if total_row else 0

            cur.execute(
                f"""
                SELECT
                    id,
                    admin_id::text,
                    action,
                    resource_type,
                    resource_id,
                    details,
                    created_at
                FROM public.admin_audit_log
                {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (*params, limit, offset),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.exception("list_admin_audit_log failed: %s", exc)
        raise HTTPException(status_code=502, detail="Audit log read failed") from exc

    logs: list[AdminAuditLogEntry] = []
    for row in rows:
        details = row[5]
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except json.JSONDecodeError:
                details = None
        logs.append(
            AdminAuditLogEntry(
                id=int(row[0]),
                admin_id=str(row[1]),
                action=str(row[2]),
                resource_type=str(row[3]),
                resource_id=row[4],
                details=details if isinstance(details, dict) else None,
                created_at=row[6],
            )
        )
    return AdminAuditLogResponse(logs=logs, total=total)


@router.get("/health")
def admin_health() -> dict:
    """Ops probe — no auth, exempt from ADMIN_ALLOWED_IPS."""
    return {"ok": True, "service": "admin"}


@router.get("/me", response_model=AdminMeResponse)
def admin_me(admin: dict = Depends(get_current_admin)) -> AdminMeResponse:
    """Current admin identity (for UI permissions without decoding JWT)."""
    return AdminMeResponse(
        admin_id=str(admin.get("sub") or ""),
        email=str(admin.get("email") or ""),
        role=str(admin.get("admin_role") or "viewer"),
    )


@router.get("/ui")
def admin_ui_page() -> FileResponse:
    """A6: Minimal admin console (HTML). Not linked from the consumer app."""
    index = _ADMIN_UI_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Admin UI not found")
    return FileResponse(index, media_type="text/html")


@router.get("/staff", response_model=AdminStaffListResponse)
def admin_list_staff(
    admin: dict = Depends(get_current_admin),
) -> AdminStaffListResponse:
    """List admin console accounts (owner/editor can view)."""
    role = str(admin.get("admin_role") or "viewer")
    if role not in _MUTATION_ROLES | {_OWNER_ROLE}:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    rows = list_admin_staff()
    return AdminStaffListResponse(
        staff=[AdminStaffMember(**row) for row in rows],
    )


@router.post("/staff", response_model=AdminInviteStaffResponse)
def admin_invite_staff(
    request: Request,
    body: AdminInviteStaffRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminInviteStaffResponse:
    """Invite admin by email — they set password + authenticator via link (owner only)."""
    _require_owner_role(admin)

    email = body.email.strip().lower()
    new_id, plain_token, error = create_admin_invite(email, body.role)
    if error or not new_id or not plain_token:
        raise HTTPException(status_code=400, detail=error or "Invite failed")

    invite_link = build_admin_invite_url(plain_token)
    email_sent, _send_err = send_admin_invite_email(email, invite_link, body.role)

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="ADMIN_USER_CREATE",
        resource_type="admin",
        resource_id=new_id,
        details={
            "email": email,
            "role": body.role,
            "invite": True,
            "email_sent": email_sent,
        },
        request=request,
        http_status_code=200,
    )

    return AdminInviteStaffResponse(
        ok=True,
        admin_id=new_id,
        email=email,
        role=body.role,
        email_sent=email_sent,
        invite_link=None if email_sent else invite_link,
    )


@router.post(
    "/staff/{admin_id}/send-password-reset",
    response_model=AdminSendPasswordResetResponse,
)
def admin_send_password_reset(
    admin_id: str,
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> AdminSendPasswordResetResponse:
    """Owner sends password reset link to an existing admin (owner only)."""
    _require_owner_role(admin)

    if str(admin.get("sub")) == admin_id:
        raise HTTPException(
            status_code=400,
            detail="Use forgot password on the login page for your own account",
        )

    email, plain_token, error = request_password_reset_by_admin_id(admin_id)
    if error or not email or not plain_token:
        raise HTTPException(status_code=400, detail=error or "Reset failed")

    reset_link = build_admin_password_reset_url(plain_token)
    email_sent, _send_err = send_admin_password_reset_email(email, reset_link)

    return AdminSendPasswordResetResponse(
        ok=True,
        admin_id=admin_id,
        email=email,
        email_sent=email_sent,
        reset_link=None if email_sent else reset_link,
    )


@router.patch("/staff/{admin_id}", response_model=AdminUpdateStaffResponse)
def admin_update_staff_member(
    admin_id: str,
    request: Request,
    body: AdminUpdateStaffRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminUpdateStaffResponse:
    """Change admin role and/or active status (owner only)."""
    _require_owner_role(admin)

    if str(admin.get("sub")) == admin_id and body.is_active is False:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    updated, error = update_admin_staff(
        admin_id,
        role=body.role,
        is_active=body.is_active,
    )
    if error or not updated:
        raise HTTPException(status_code=400, detail=error or "Update failed")

    previous_role = str(updated.get("previous_role") or updated["role"])
    previous_active = bool(updated.get("previous_is_active", updated["is_active"]))

    if body.role is not None and previous_role != updated["role"]:
        write_admin_audit_log(
            admin_id=str(admin["sub"]),
            action="ADMIN_ROLE_CHANGE",
            resource_type="admin",
            resource_id=str(updated["admin_id"]),
            details={
                "email": updated["email"],
                "previous_role": previous_role,
                "new_role": updated["role"],
            },
            request=request,
            http_status_code=200,
        )

    if body.is_active is not None and previous_active != updated["is_active"]:
        audit_action = (
            "ADMIN_USER_ACTIVATE" if updated["is_active"] else "ADMIN_USER_DEACTIVATE"
        )
        write_admin_audit_log(
            admin_id=str(admin["sub"]),
            action=audit_action,
            resource_type="admin",
            resource_id=str(updated["admin_id"]),
            details={
                "email": updated["email"],
                "previous_is_active": previous_active,
                "is_active": updated["is_active"],
            },
            request=request,
            http_status_code=200,
        )

    return AdminUpdateStaffResponse(
        ok=True,
        admin_id=str(updated["admin_id"]),
        email=str(updated["email"]),
        role=str(updated["role"]),
        is_active=bool(updated["is_active"]),
        previous_role=previous_role,
        previous_is_active=previous_active,
    )


@router.delete("/staff/{admin_id}", response_model=AdminDeleteStaffResponse)
def admin_delete_staff_member(
    admin_id: str,
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> AdminDeleteStaffResponse:
    """Permanently remove an admin account (owner only)."""
    _require_owner_role(admin)

    if str(admin.get("sub")) == admin_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    deleted, error = delete_admin_user(admin_id)
    if error or not deleted:
        raise HTTPException(status_code=400, detail=error or "Delete failed")

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="ADMIN_USER_DELETE",
        resource_type="admin",
        resource_id=str(deleted["admin_id"]),
        details={
            "email": deleted["email"],
            "role": deleted["role"],
            "is_active": deleted["is_active"],
            "totp_enabled": deleted["totp_enabled"],
        },
        request=request,
        http_status_code=200,
    )

    return AdminDeleteStaffResponse(
        ok=True,
        admin_id=str(deleted["admin_id"]),
        email=str(deleted["email"]),
        role=str(deleted["role"]),
    )


@router.get("/courses", response_model=AdminCoursesResponse)
def admin_list_courses(
    _admin: dict = Depends(get_current_admin),
) -> AdminCoursesResponse:
    """All courses (draft + published), Stripe prices, bundle children."""
    return list_admin_courses()


@router.get("/courses/{course_slug}", response_model=AdminCourseDetailResponse)
def admin_get_course(
    course_slug: str,
    _admin: dict = Depends(get_current_admin),
) -> AdminCourseDetailResponse:
    """Full course aggregate for the CMS editor."""
    try:
        detail = get_admin_course_detail(course_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return detail


@router.post("/courses", response_model=AdminCourseDetailResponse)
def admin_create_course_endpoint(
    request: Request,
    body: AdminCreateCourseRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminCourseDetailResponse:
    """Create course shell (slug, title, publish flag)."""
    _require_mutation_role(admin)
    try:
        detail = create_admin_course(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="CREATE_COURSE",
        resource_type="course",
        resource_id=detail.course_slug,
        details={"title": detail.title, "is_published": detail.is_published},
        request=request,
        http_status_code=200,
    )
    return detail


@router.patch("/courses/{course_slug}", response_model=AdminCourseDetailResponse)
def admin_update_course_endpoint(
    course_slug: str,
    request: Request,
    body: AdminUpdateCourseRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminCourseDetailResponse:
    """Update title, description, and/or publish flag."""
    _require_mutation_role(admin)
    try:
        previous = get_admin_course_detail(course_slug)
        detail = update_admin_course(course_slug, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    audit_action = "UPDATE_COURSE"
    if body.is_published is True and previous and not previous.is_published:
        audit_action = "PUBLISH_COURSE"

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action=audit_action,
        resource_type="course",
        resource_id=course_slug,
        details={
            "title": detail.title,
            "is_published": detail.is_published,
            "fields": body.model_dump(exclude_none=True),
        },
        request=request,
        http_status_code=200,
    )
    return detail


@router.put("/courses/{course_slug}/schedule", response_model=AdminScheduleReplaceResponse)
def admin_replace_schedule_endpoint(
    course_slug: str,
    request: Request,
    body: AdminReplaceScheduleRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminScheduleReplaceResponse:
    """Replace all daily schedule days for a course."""
    _require_mutation_role(admin)
    try:
        count = replace_admin_course_schedule(course_slug, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="UPDATE_COURSE",
        resource_type="course",
        resource_id=course_slug,
        details={"schedule_days": count},
        request=request,
        http_status_code=200,
    )
    return AdminScheduleReplaceResponse(
        ok=True,
        course_slug=course_slug,
        day_count=count,
    )


@router.put("/courses/{course_slug}/weeks", response_model=AdminCourseDetailResponse)
def admin_replace_weeks_endpoint(
    course_slug: str,
    request: Request,
    body: AdminReplaceWeeksRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminCourseDetailResponse:
    """Replace week/lesson catalog tree for storefront."""
    _require_mutation_role(admin)
    try:
        detail = replace_admin_course_weeks(course_slug, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="UPDATE_COURSE",
        resource_type="course",
        resource_id=course_slug,
        details={"week_count": len(body.weeks)},
        request=request,
        http_status_code=200,
    )
    return detail


@router.patch("/courses/{course_slug}/product", response_model=AdminCourseDetailResponse)
def admin_upsert_product_endpoint(
    course_slug: str,
    request: Request,
    body: AdminUpsertProductRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminCourseDetailResponse:
    """Upsert Stripe product/price row in course_products."""
    _require_mutation_role(admin)
    try:
        detail = upsert_admin_course_product(course_slug, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="UPDATE_COURSE",
        resource_type="course",
        resource_id=course_slug,
        details={
            "provider_price_id": body.provider_price_id,
            "unit_amount_cents": body.unit_amount_cents,
        },
        request=request,
        http_status_code=200,
    )
    return detail


@router.patch("/courses/{course_slug}/voice", response_model=AdminCourseDetailResponse)
def admin_upsert_voice_endpoint(
    course_slug: str,
    request: Request,
    body: AdminUpsertVoiceRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminCourseDetailResponse:
    """Set ElevenLabs (or other) voice_id for course TTS."""
    _require_mutation_role(admin)
    try:
        detail = upsert_admin_course_voice(course_slug, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="UPDATE_COURSE",
        resource_type="course",
        resource_id=course_slug,
        details={
            "voice_provider": body.provider,
            "voice_id_hint": f"…{body.voice_id.strip()[-4:]}"
            if len(body.voice_id.strip()) > 4
            else body.voice_id.strip(),
        },
        request=request,
        http_status_code=200,
    )
    return detail


@router.get("/users/{user_id}", response_model=AdminUserDetailResponse)
def admin_get_user(
    user_id: str,
    _admin: dict = Depends(get_current_admin),
) -> AdminUserDetailResponse:
    """One app user: profile, entitlements, purchases, usage, recent analytics."""
    return get_admin_user_detail(user_id)


@router.get("/users", response_model=AdminUsersResponse)
def admin_list_users(
    request: Request,
    q: str = "",
    limit: int = 50,
    _admin: dict = Depends(get_current_admin),
) -> AdminUsersResponse:
    """A4: Find users by email prefix."""
    users = search_admin_users(query=q, limit=limit)
    return AdminUsersResponse(users=users)


@router.post("/entitlements/grant", response_model=AdminEntitlementMutationResponse)
def admin_grant_endpoint(
    request: Request,
    body: AdminGrantEntitlementRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminEntitlementMutationResponse:
    """A3: Grant course or bundle (expands bundle to included courses)."""
    _require_mutation_role(admin)
    user_id = _normalize_user_id(body.user_id)
    granted = admin_grant_entitlements(user_id, body.course_slug, note=body.note)

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="GRANT_ENTITLEMENT",
        resource_type="entitlement",
        resource_id=user_id,
        details={
            "course_slug": body.course_slug,
            "granted_slugs": granted,
            "note": body.note,
        },
        request=request,
        http_status_code=200,
    )

    return AdminEntitlementMutationResponse(ok=bool(granted), granted_slugs=granted)


@router.post("/entitlements/revoke", response_model=AdminEntitlementMutationResponse)
def admin_revoke_endpoint(
    request: Request,
    body: AdminRevokeEntitlementRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminEntitlementMutationResponse:
    """A3: Revoke course or bundle (expands bundle to included courses)."""
    _require_mutation_role(admin)
    user_id = _normalize_user_id(body.user_id)
    reason = (body.reason or "admin_revoke").strip() or "admin_revoke"
    revoked = admin_revoke_entitlements(user_id, body.course_slug, reason=reason)

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="REVOKE_ENTITLEMENT",
        resource_type="entitlement",
        resource_id=user_id,
        details={
            "course_slug": body.course_slug,
            "revoked_slugs": revoked,
            "reason": reason,
        },
        request=request,
        http_status_code=200,
    )

    return AdminEntitlementMutationResponse(ok=bool(revoked), revoked_slugs=revoked)


@router.get("/audit-log", response_model=AdminAuditLogResponse)
def admin_audit_log_endpoint(
    limit: int = 100,
    offset: int = 0,
    action: Optional[str] = None,
    _admin: dict = Depends(get_current_admin),
) -> AdminAuditLogResponse:
    """A5: Paginated admin audit log with optional action filter."""
    return list_admin_audit_log(limit=limit, offset=offset, action=action)


@router.get("/analytics/summary", response_model=AdminAnalyticsSummaryResponse)
def admin_analytics_summary_endpoint(
    days: int = 7,
    _admin: dict = Depends(get_current_admin),
) -> AdminAnalyticsSummaryResponse:
    """Phase 3: product + AI ops rollup (events, RAG, quota pressure)."""
    try:
        return get_admin_analytics_summary(days=days)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("admin analytics summary failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Analytics summary read failed",
        ) from exc


@router.get("/schedules/{course_slug}", response_model=AdminScheduleHealthResponse)
def admin_schedule_health_endpoint(
    course_slug: str,
    _admin: dict = Depends(get_current_admin),
) -> AdminScheduleHealthResponse:
    """Phase 3: verify daily schedule rows imported for a course."""
    try:
        return get_admin_schedule_health(course_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("admin schedule health failed slug=%s: %s", course_slug, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Schedule health read failed",
        ) from exc


@router.get("/waitlist/stats", response_model=AdminWaitlistStatsResponse)
def admin_waitlist_stats_endpoint(
    _admin: dict = Depends(get_current_admin),
) -> AdminWaitlistStatsResponse:
    """Website waitlist totals (marketing signups in shared Supabase)."""
    if not waitlist_table_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Waitlist table not available — run backend/sql/supabase_waitlist_signups.sql",
        )
    try:
        return get_waitlist_stats()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.get("/waitlist", response_model=AdminWaitlistListResponse)
def admin_waitlist_list_endpoint(
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    pending_only: bool = False,
    _admin: dict = Depends(get_current_admin),
) -> AdminWaitlistListResponse:
    """Paginated website waitlist emails."""
    if not waitlist_table_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Waitlist table not available",
        )
    try:
        return list_waitlist_signups(
            query=q,
            limit=limit,
            offset=offset,
            pending_only=pending_only,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.get("/waitlist/export")
def admin_waitlist_export_endpoint(
    request: Request,
    q: str = "",
    pending_only: bool = False,
    admin: dict = Depends(get_current_admin),
) -> PlainTextResponse:
    """CSV export of waitlist signups (audit logged)."""
    if not waitlist_table_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Waitlist table not available",
        )
    try:
        csv_body = export_waitlist_csv(query=q, pending_only=pending_only)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="EXPORT_DATA",
        resource_type="waitlist",
        resource_id="export",
        details={"query": q[:80] if q else None, "pending_only": pending_only},
        request=request,
        http_status_code=200,
    )
    return PlainTextResponse(
        content=csv_body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="waitlist_signups.csv"'},
    )
