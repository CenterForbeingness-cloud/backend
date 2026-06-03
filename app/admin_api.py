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

from app.admin_auth import list_admin_staff, update_admin_role, verify_admin_token
from app.config import FAIR_USE_LIMIT, SUPABASE_DB_URL, logger
from app.daily_schedule import validate_course_slug
from app.entitlements import (
    entitlement_grants_for_product,
    get_user_entitlements,
    grant_entitlement,
    resolve_chat_plan,
    revoke_entitlement,
)
from app.models import (
    AdminAuditLogEntry,
    AdminAuditLogResponse,
    AdminEntitlementMutationResponse,
    AdminGrantEntitlementRequest,
    AdminRevokeEntitlementRequest,
    AdminStaffListResponse,
    AdminStaffMember,
    AdminUpdateRoleRequest,
    AdminUpdateRoleResponse,
    AdminUserSummary,
    AdminUsersResponse,
)
from app.quotas import get_usage_info

_admin_bearer = HTTPBearer(auto_error=True)

router = APIRouter(prefix="/admin", tags=["admin"])

_MUTATION_ROLES = frozenset({"owner", "editor"})
_OWNER_ROLE = "owner"

_ADMIN_UI_DIR = Path(__file__).resolve().parent.parent / "static" / "admin"


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


def list_admin_audit_log(*, limit: int, offset: int) -> AdminAuditLogResponse:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        from app.db import db_connection

        with db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM public.admin_audit_log")
            total_row = cur.fetchone()
            total = int(total_row[0]) if total_row else 0

            cur.execute(
                """
                SELECT
                    id,
                    admin_id::text,
                    action,
                    resource_type,
                    resource_id,
                    details,
                    created_at
                FROM public.admin_audit_log
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
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


@router.patch("/staff/{admin_id}", response_model=AdminUpdateRoleResponse)
def admin_update_staff_role(
    admin_id: str,
    request: Request,
    body: AdminUpdateRoleRequest,
    admin: dict = Depends(get_current_admin),
) -> AdminUpdateRoleResponse:
    """Change admin_users.role (owner only)."""
    _require_owner_role(admin)
    updated, error = update_admin_role(admin_id, body.role)
    if error or not updated:
        raise HTTPException(status_code=400, detail=error or "Update failed")

    previous = str(updated.get("previous_role") or body.role)
    write_admin_audit_log(
        admin_id=str(admin["sub"]),
        action="ADMIN_ROLE_CHANGE",
        resource_type="admin",
        resource_id=str(updated["admin_id"]),
        details={
            "email": updated["email"],
            "previous_role": previous,
            "new_role": updated["role"],
        },
        request=request,
        http_status_code=200,
    )

    return AdminUpdateRoleResponse(
        ok=True,
        admin_id=str(updated["admin_id"]),
        email=str(updated["email"]),
        role=str(updated["role"]),
        previous_role=previous,
    )


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
    _admin: dict = Depends(get_current_admin),
) -> AdminAuditLogResponse:
    """A5: Paginated admin audit log."""
    return list_admin_audit_log(limit=limit, offset=offset)
