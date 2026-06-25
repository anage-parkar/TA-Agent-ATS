"""RBAC: permission matrix, the require_permission guard, and a route-level
check that an interviewer cannot reach a recruiter/admin-only action."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.rbac import has_permission, require_permission
from services.tenant_context import Principal, use_principal


def test_permission_matrix():
    assert has_permission("recruiter", "application.score")
    assert has_permission("admin", "application.score")
    assert not has_permission("interviewer", "application.score")
    assert not has_permission("coordinator", "application.score")

    assert has_permission("admin", "tenant.admin")
    assert not has_permission("recruiter", "tenant.admin")

    # interviewers participate in scorecards but nothing else mutating
    assert has_permission("interviewer", "scorecard.write")
    assert not has_permission("interviewer", "email.send")

    # undeclared permission = readable by any known role
    assert has_permission("interviewer", "dashboard.view")


def test_require_permission_blocks_then_allows():
    guard = require_permission("application.score")
    with use_principal(Principal("t", "u", "interviewer")):
        with pytest.raises(HTTPException) as exc:
            guard()
        assert exc.value.status_code == 403
    with use_principal(Principal("t", "u", "recruiter")):
        guard()  # no exception


def test_admin_route_enforces_role():
    import main

    client = TestClient(main.app)
    tid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    # interviewer is blocked from the admin-only purge endpoint
    blocked = client.request("DELETE", f"/api/admin/tenants/{tid}", headers={"X-User-Role": "interviewer"})
    assert blocked.status_code == 403

    # admin (default role when no header) is allowed
    allowed = client.request("DELETE", f"/api/admin/tenants/{tid}", headers={"X-User-Role": "admin"})
    assert allowed.status_code == 200
    assert allowed.json()["tenant_id"] == tid
