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

from fastapi import HTTPException, Request

from services import security_events
from services.config import settings
from services.rbac import ROLES
from services.tenant_context import SYSTEM_ROLE, SYSTEM_USER_PREFIX, Principal, set_principal

logger = logging.getLogger("ta_agent.auth")

# APP_ENV values in which dev-only conveniences (trusted X-User-* headers) are
# permitted. Anything else is treated as production.
DEV_LIKE_ENVS = frozenset({"dev", "development", "local", "test", "testing", "ci"})


class InsecureAuthConfig(RuntimeError):
    """The auth configuration would let any caller impersonate any
    tenant/user/role outside a dev-like environment. Raised at startup."""


def assert_safe_auth_config(cfg=None) -> None:
    """Refuse to start with trusted request headers outside dev/test.

    Called from the app lifespan (main.py) so `uvicorn main:app` fails fast
    with the message below instead of serving an impersonable API. An explicit
    ALLOW_TRUSTED_HEADERS_OUTSIDE_DEV=true override is honoured but recorded as
    a critical security event every boot.
    """
    cfg = settings if cfg is None else cfg
    env = (cfg.app_env or "").strip().lower()
    if not cfg.trust_auth_headers or env in DEV_LIKE_ENVS:
        return
    shown = repr(env) if env else "<unset: treated as production>"
    if cfg.allow_trusted_headers_outside_dev:
        security_events.record("auth.trusted_headers_override", severity="critical", app_env=shown)
        logger.critical(
            "TRUST_AUTH_HEADERS=true in APP_ENV=%s with ALLOW_TRUSTED_HEADERS_OUTSIDE_DEV=true: "
            "every caller can claim any tenant/user/role. Acceptable ONLY behind a front door "
            "that strips inbound X-User-* headers and sets them itself.",
            shown,
        )
        return
    raise InsecureAuthConfig(
        f"Refusing to start: TRUST_AUTH_HEADERS=true in APP_ENV={shown}. With trusted "
        "headers, any caller can act as any tenant, user or role. Fix one of: "
        "TRUST_AUTH_HEADERS=false (real auth); APP_ENV=dev|test|ci for local/dev work; "
        "or — ONLY behind a front door that strips inbound X-User-* headers and sets "
        "them itself — ALLOW_TRUSTED_HEADERS_OUTSIDE_DEV=true."
    )


def _request_meta(request: Request) -> dict:
    return {
        "path": request.url.path,
        "method": request.method,
        "client": request.client.host if request.client else None,
    }


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

    # The automation identity (role SYSTEM_ROLE, user_id "system:<actor>") is
    # reserved for scheduled jobs bound in-process via tenant_context.use_system().
    # No HTTP caller may claim it: the role is not in ROLES so it falls to least
    # privilege below, and the reserved user-id namespace is refused outright
    # rather than silently re-attributed to someone else.
    if user.strip().lower().startswith(SYSTEM_USER_PREFIX):
        security_events.record(
            "auth.system_identity_claimed",
            claimed_user_id=user, claimed_role=role, claimed_tenant=tenant,
            **_request_meta(request),
        )
        raise HTTPException(status_code=401, detail="reserved identity")

    if role == SYSTEM_ROLE:
        security_events.record(
            "auth.system_role_claimed",
            claimed_role=role, claimed_user_id=user, claimed_tenant=tenant,
            **_request_meta(request),
        )

    if role not in ROLES:
        # Unknown/spoofed role (including 'system') → least privilege rather
        # than failing the request.
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
