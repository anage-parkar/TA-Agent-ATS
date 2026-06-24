"""Request authentication / tenant resolution (Workstream A — scaffolded).

`bind_request_context` is an app-level FastAPI dependency that resolves the
caller's tenant + user + role and binds them to the request-scoped context
(services.tenant_context) for the lifetime of the request.

Today identity comes from request headers (X-Tenant-Id / X-User-Id /
X-User-Role) with a fallback to the seeded default org+admin so the existing
local flow keeps working. This is the seam: when real authentication lands
(email+invite first, SSO/SAML + SCIM later), only this resolver changes — the
contextvar, RLS, and RBAC layers stay the same.
"""

from __future__ import annotations

import logging

from fastapi import Request

from services.config import settings
from services.rbac import ROLES
from services.tenant_context import Principal, set_principal

logger = logging.getLogger("ta_agent.auth")


def _resolve_principal(request: Request) -> Principal:
    tenant = settings.default_tenant_id
    user = settings.default_user_id
    role = "admin"

    if settings.trust_auth_headers:
        tenant = request.headers.get("X-Tenant-Id") or tenant
        user = request.headers.get("X-User-Id") or user
        header_role = request.headers.get("X-User-Role")
        if header_role:
            role = header_role.strip().lower()

    if role not in ROLES:
        # Unknown/spoofed role → least privilege rather than failing the request.
        logger.warning("unknown role %r; defaulting to interviewer", role)
        role = "interviewer"

    return Principal(tenant_id=tenant, user_id=user, role=role)


async def bind_request_context(request: Request):
    """App-level dependency: bind the principal for this request.

    MUST be async. A sync dependency runs in a threadpool whose contextvar
    changes don't propagate back, so the principal would be lost. As an async
    dependency it runs in the request's own task context; anyio then copies that
    context (principal included) into the sync sub-dependencies (require_permission)
    and the sync endpoint — where the repository reads the tenant. Each request
    is its own task context, so the bare set() is isolated; no reset needed.
    """
    set_principal(_resolve_principal(request))
