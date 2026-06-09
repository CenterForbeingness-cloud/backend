"""Admin API helpers (minimal v1)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.admin_access import enforce_admin_ip
from app.admin_api import (
    admin_grant_entitlements,
    admin_revoke_entitlements,
    get_admin_user_detail,
    get_current_admin,
)
from app.entitlements import entitlement_grants_for_product
from app.main import app


def test_entitlement_grants_for_bundle_expands_children() -> None:
    slugs = entitlement_grants_for_product("starter-bundle")
    assert "starter-bundle" in slugs
    assert "mindful-foundations" in slugs
    assert "week-zero-reset" in slugs


def test_get_current_admin_rejects_invalid_token() -> None:
    creds = MagicMock()
    creds.credentials = "not-a-valid-token"
    with patch("app.admin_api.verify_admin_token", return_value=None):
        with pytest.raises(HTTPException) as exc:
            get_current_admin(creds)
    assert exc.value.status_code == 401


def test_admin_grant_calls_grant_for_each_slug() -> None:
    with patch(
        "app.admin_api.entitlement_grants_for_product",
        return_value=["week-zero-reset"],
    ), patch("app.admin_api.grant_entitlement", return_value=True) as mock_grant:
        granted = admin_grant_entitlements(
            "00000000-0000-0000-0000-000000000001",
            "week-zero-reset",
        )
    assert granted == ["week-zero-reset"]
    mock_grant.assert_called_once()
    assert mock_grant.call_args.kwargs["granted_by"] == "admin"


def test_admin_revoke_calls_revoke_for_bundle_children() -> None:
    children = entitlement_grants_for_product("starter-bundle")
    with patch("app.admin_api.revoke_entitlement", return_value=True) as mock_revoke:
        revoked = admin_revoke_entitlements(
            "00000000-0000-0000-0000-000000000001",
            "starter-bundle",
            reason="support_refund",
        )
    assert set(revoked) == set(children)
    assert mock_revoke.call_count == len(children)


def test_enforce_admin_ip_blocks_when_allowlist_set() -> None:
    request = MagicMock()
    request.url.path = "/admin/users"
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "203.0.113.9"

    with patch("app.admin_access.admin_allowed_ips", return_value=frozenset({"203.0.113.1"})):
        with pytest.raises(HTTPException) as exc:
            enforce_admin_ip(request)
    assert exc.value.status_code == 403


def test_enforce_admin_ip_allows_health_probe() -> None:
    request = MagicMock()
    request.url.path = "/admin/health"
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "203.0.113.9"

    with patch("app.admin_access.admin_allowed_ips", return_value=frozenset({"203.0.113.1"})):
        enforce_admin_ip(request)


def test_admin_me_shape() -> None:
    from app.admin_api import admin_me

    payload = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "email": "ops@example.com",
        "admin_role": "editor",
    }
    resp = admin_me(payload)
    assert resp.admin_id == payload["sub"]
    assert resp.email == "ops@example.com"
    assert resp.role == "editor"


def test_admin_health_unauthenticated() -> None:
    client = TestClient(app)
    res = client.get("/admin/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "service": "admin"}


def test_admin_me_requires_bearer() -> None:
    client = TestClient(app)
    res = client.get("/admin/me")
    assert res.status_code == 403


def test_admin_user_detail_invalid_uuid() -> None:
    client = TestClient(app)
    with patch("app.admin_api.verify_admin_token", return_value={"sub": "a", "email": "a@b.c", "admin_role": "viewer", "type": "admin"}):
        res = client.get(
            "/admin/users/not-a-uuid",
            headers={"Authorization": "Bearer fake"},
        )
    assert res.status_code == 400


def test_get_admin_user_detail_not_found() -> None:
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = None

    with patch("app.admin_api.SUPABASE_DB_URL", "postgres://test"), patch(
        "app.db.db_connection", return_value=mock_conn
    ), patch.object(mock_conn, "cursor", return_value=mock_cur):
        with pytest.raises(HTTPException) as exc:
            get_admin_user_detail("00000000-0000-0000-0000-000000000099")
    assert exc.value.status_code == 404


def test_get_admin_user_detail_skips_missing_analytics() -> None:
    uid = "00000000-0000-0000-0000-000000000001"
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)

    def execute_side_effect(sql, params=None):
        if "auth.users" in sql:
            mock_cur.fetchone.return_value = ("user@example.com",)
        elif "user_entitlements" in sql:
            mock_cur.fetchall.return_value = [
                ("week-zero-reset", datetime.now(timezone.utc), "stripe", None, None, None),
            ]
        elif "course_purchases" in sql and "purchase_source" in sql:
            mock_cur.fetchall.return_value = []
        elif "analytics_events" in sql:
            raise Exception('relation "analytics_events" does not exist')

    mock_cur.execute.side_effect = execute_side_effect

    with patch("app.admin_api.SUPABASE_DB_URL", "postgres://test"), patch(
        "app.db.db_connection", return_value=mock_conn
    ), patch.object(mock_conn, "cursor", return_value=mock_cur), patch(
        "app.admin_api.get_user_profile", return_value=None
    ), patch(
        "app.admin_api.get_usage_info",
        return_value={"messages_today": 0, "limit": 100, "reset_at": None},
    ), patch("app.admin_api.resolve_chat_plan", return_value="free"):
        detail = get_admin_user_detail(uid)

    assert detail.email == "user@example.com"
    assert detail.recent_events == []
    assert len(detail.entitlements) == 1


def test_admin_ip_middleware_blocks_when_configured() -> None:
    client = TestClient(app)
    with patch.dict("os.environ", {"ADMIN_ALLOWED_IPS": "198.51.100.1"}, clear=False):
        res = client.get("/admin/health")
    assert res.status_code == 200
    with patch.dict("os.environ", {"ADMIN_ALLOWED_IPS": "198.51.100.1"}, clear=False):
        res = client.get("/admin/me", headers={"Authorization": "Bearer x"})
    assert res.status_code == 403
    assert res.json()["detail"] == "Admin access restricted by IP"


def test_list_admin_courses_includes_bundle_children() -> None:
    from app.admin_api import list_admin_courses

    with patch(
        "app.admin_api.list_courses",
        return_value=[
            {
                "course_slug": "starter-bundle",
                "title": "Starter Bundle",
                "price_id": "price_bundle",
            },
        ],
    ):
        resp = list_admin_courses()
    slugs = {c.course_slug for c in resp.courses}
    assert "starter-bundle" in slugs
    bundle = next(c for c in resp.courses if c.course_slug == "starter-bundle")
    assert "week-zero-reset" in bundle.bundle_included_slugs


def test_list_admin_audit_log_filters_action() -> None:
    from app.admin_api import list_admin_audit_log

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = (0,)
    mock_cur.fetchall.return_value = []

    with patch("app.admin_api.SUPABASE_DB_URL", "postgres://test"), patch(
        "app.db.db_connection", return_value=mock_conn
    ), patch.object(mock_conn, "cursor", return_value=mock_cur):
        list_admin_audit_log(limit=10, offset=0, action="ADMIN_LOGIN")

    count_sql = mock_cur.execute.call_args_list[0][0][0]
    assert "action = %s" in count_sql


def test_list_admin_audit_log_rejects_unknown_action() -> None:
    from app.admin_api import list_admin_audit_log

    with pytest.raises(HTTPException) as exc:
        list_admin_audit_log(limit=10, offset=0, action="HACK")
    assert exc.value.status_code == 400


def test_admin_analytics_summary_rag_miss_rate() -> None:
    from app.admin_ops import get_admin_analytics_summary

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)

    def table_side_effect(sql, params=None):
        sql_l = sql.lower()
        if "information_schema.tables" in sql_l:
            mock_cur.fetchone.return_value = (1,)
        elif "auth.users limit" in sql_l:
            pass
        elif "group by event_name" in sql_l:
            mock_cur.fetchall.return_value = [
                ("rag_retrieval", 8),
                ("rag_retrieval_miss", 2),
                ("purchase_completed", 1),
            ]
        elif "count(*) from auth.users" in sql_l:
            mock_cur.fetchone.return_value = (3,)
        elif "from public.user_profile" in sql_l and "updated_at" in sql_l:
            mock_cur.fetchone.return_value = (2,)
        elif "from public.user_profile" in sql_l:
            mock_cur.fetchone.return_value = (5,)
        elif "from public.chat_messages" in sql_l:
            mock_cur.fetchone.return_value = (40,)
        elif "from public.user_message_counts" in sql_l:
            mock_cur.fetchall.return_value = []
        elif "voice_seconds_today" in sql_l:
            mock_cur.fetchone.return_value = (0,)
        elif "voice_session_end" in sql_l:
            mock_cur.fetchone.return_value = (120.5,)
        else:
            mock_cur.fetchone.return_value = (0,)
            mock_cur.fetchall.return_value = []

    mock_cur.execute.side_effect = table_side_effect

    with patch("app.admin_ops.SUPABASE_DB_URL", "postgres://test"), patch(
        "app.db.db_connection", return_value=mock_conn
    ), patch.object(mock_conn, "cursor", return_value=mock_cur):
        summary = get_admin_analytics_summary(days=7)

    assert summary.rag_health.hits == 8
    assert summary.rag_health.misses == 2
    assert summary.rag_health.miss_rate_pct == 20.0
    assert summary.purchases_completed == 1


def test_admin_schedule_health_endpoint() -> None:
    client = TestClient(app)
    with patch("app.admin_api.verify_admin_token", return_value={"sub": "a", "type": "admin"}):
        with patch(
            "app.admin_api.get_admin_schedule_health",
            return_value=__import__(
                "app.models", fromlist=["AdminScheduleHealthResponse"]
            ).AdminScheduleHealthResponse(
                course_slug="mindful-foundations",
                day_count=1,
                days=[],
            ),
        ):
            res = client.get(
                "/admin/schedules/mindful-foundations",
                headers={"Authorization": "Bearer x"},
            )
    assert res.status_code == 200
    assert res.json()["course_slug"] == "mindful-foundations"


def test_admin_courses_endpoint() -> None:
    client = TestClient(app)
    with patch("app.admin_api.verify_admin_token", return_value={"sub": "a", "type": "admin"}):
        with patch(
            "app.admin_api.list_admin_courses",
            return_value=__import__("app.models", fromlist=["AdminCoursesResponse"]).AdminCoursesResponse(courses=[]),
        ):
            res = client.get("/admin/courses", headers={"Authorization": "Bearer x"})
    assert res.status_code == 200


def test_update_admin_role_validates_role() -> None:
    from app.admin_auth import update_admin_role

    with patch("app.admin_auth._get_db_connection"):
        updated, error = update_admin_role(
            "00000000-0000-0000-0000-000000000099",
            "superadmin",
        )
    assert updated is None
    assert error is not None
