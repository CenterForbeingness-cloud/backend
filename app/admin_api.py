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
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.admin_auth import (
    create_admin_user,
    generate_totp_secret,
    list_admin_staff,
    set_totp_secret,
    update_admin_staff,
    verify_admin_token,
)
from app.config import ADMIN_2FA_ISSUER, FAIR_USE_LIMIT, STRIPE_PRICE_BY_COURSE_SLUG, SUPABASE_DB_URL, logger
from app.courses import list_courses
from app.entitlements import BUNDLE_INCLUDED_COURSES
from app.user_profile import get_user_profile
from app.daily_schedule import validate_course_slug
from app.entitlements import (
    entitlement_grants_for_product,
    get_user_entitlements,
    grant_entitlement,
    resolve_chat_plan,
    revoke_entitlement,
)
from app.admin_ops import get_admin_analytics_summary, get_admin_schedule_health
from app.models import (
    AdminAnalyticsEventRow,
    AdminAnalyticsSummaryResponse,
    AdminAuditLogEntry,
    AdminAuditLogResponse,
    AdminCourseItem,
    AdminCoursesResponse,
    AdminCreateStaffRequest,
    AdminCreateStaffResponse,
    AdminEntitlementMutationResponse,
    AdminEntitlementRow,
    AdminGrantEntitlementRequest,
    AdminMeResponse,
    AdminPurchaseRow,
    AdminRevokeEntitlementRequest,
    AdminScheduleHealthResponse,
    AdminStaffListResponse,
    AdminStaffMember,
    AdminUpdateStaffRequest,
    AdminUpdateStaffResponse,
    AdminUsageSnippet,
    AdminUserDetailResponse,
    AdminUserProfileSnippet,
    AdminUserSummary,
    AdminUsersResponse,
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
    """Published catalog + env price fallbacks and bundle children."""
    raw = list_courses()
    by_slug: dict[str, AdminCourseItem] = {}

    for course in raw:
        slug = str(course.get("course_slug") or "").strip()
        if not slug:
            continue
        included = list(BUNDLE_INCLUDED_COURSES.get(slug, ()))
        by_slug[slug] = AdminCourseItem(
            course_slug=slug,
            title=str(course.get("title") or slug),
            price_id=course.get("price_id"),
            is_published=True,
            bundle_included_slugs=included,
        )

    for slug in BUNDLE_INCLUDED_COURSES:
        if slug not in by_slug:
            by_slug[slug] = AdminCourseItem(
                course_slug=slug,
                title=slug.replace("-", " ").title(),
                price_id=STRIPE_PRICE_BY_COURSE_SLUG.get(slug),
                is_published=True,
                bundle_included_slugs=list(BUNDLE_INCLUDED_COURSES[slug]),
            )

    for slug, price_id in STRIPE_PRICE_BY_COURSE_SLUG.items():
        if slug not in by_slug:
            by_slug[slug] = AdminCourseItem(
                course_slug=slug,
                title=slug.replace("-", " ").title(),
                price_id=price_id,
                is_published=True,
                bundle_included_slugs=list(BUNDLE_INCLUDED_COURSES.get(slug, ())),
            )

    courses = sorted(by_slug.values(), key=lambda c: c.course_slug)
    return AdminCoursesResponse(courses=courses)


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


@router.post("/staff", response_model=AdminCreateStaffResponse)
def admin_create_staff(
    request: Request,
    body: AdminCreateStaffRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminCreateStaffResponse:
    """Create admin account and enroll TOTP (owner only)."""
    _require_owner_role(admin)

    new_id, error = create_admin_user(body.email, body.password, body.role)
    if error or not new_id:
        raise HTTPException(status_code=400, detail=error or "Create failed")

    import pyotp

    totp_secret = generate_totp_secret()
    ok, totp_err = set_totp_secret(body.email.strip(), totp_secret)
    if not ok:
        raise HTTPException(status_code=500, detail=totp_err or "Failed to set TOTP")

    totp = pyotp.TOTP(totp_secret)
    provisioning_uri = totp.provisioning_uri(
        name=body.email.strip(),
        issuer_name=ADMIN_2FA_ISSUER,
    )

    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="ADMIN_USER_CREATE",
        resource_type="admin",
        resource_id=new_id,
        details={"email": body.email.strip(), "role": body.role},
        request=request,
        http_status_code=200,
    )

    return AdminCreateStaffResponse(
        ok=True,
        admin_id=new_id,
        email=body.email.strip(),
        role=body.role,
        totp_secret=totp_secret,
        totp_provisioning_uri=provisioning_uri,
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


@router.get("/courses", response_model=AdminCoursesResponse)
def admin_list_courses(
    _admin: dict = Depends(get_current_admin),
) -> AdminCoursesResponse:
    """Published courses, Stripe price IDs, and bundle children."""
    return list_admin_courses()


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
