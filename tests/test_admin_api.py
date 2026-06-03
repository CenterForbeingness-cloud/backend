"""Admin API helpers (minimal v1)."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.admin_api import (
    admin_grant_entitlements,
    admin_revoke_entitlements,
    get_current_admin,
)
from app.entitlements import entitlement_grants_for_product


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


def test_update_admin_role_validates_role() -> None:
    from app.admin_auth import update_admin_role

    with patch("app.admin_auth._get_db_connection"):
        updated, error = update_admin_role(
            "00000000-0000-0000-0000-000000000099",
            "superadmin",
        )
    assert updated is None
    assert error is not None
