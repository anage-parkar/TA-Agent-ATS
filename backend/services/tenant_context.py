"""Request-scoped tenant + identity context (Workstream A).

A `Principal` (tenant_id, user_id, role) is bound per request via a contextvar
set in an app-level dependency (see services/auth.py). The repository reads
`get_tenant_id()` to scope the in-memory store and to set the Postgres
`app.tenant_id` GUC that RLS policies key off — so tenant isolation does not
depend on every query remembering a WHERE clause.

Outside a request (Celery tasks, pollers, scripts) nothing is bound, so
`get_principal()` falls back to the seeded default org/admin. Background jobs
that act for a specific tenant must wrap their work in `use_system(tenant_id)`:
a least-privilege *system* principal that scopes the tenant (in-memory filter /
Postgres RLS) but holds no RBAC permissions, is never treated as a human actor
(so the rejection gate still blocks it), and is audited as actor_type='system'.
Operators/tests that act *as a person* for a tenant use `use_tenant(...)`.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass

from services.config import settings


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    role: str


# Role reserved for unattended automation (schedulers, pollers). It is NOT in
# services.rbac.ROLES, so it can never be obtained over HTTP (auth downgrades
# unknown roles) and holds no permissions; it exists only to scope a tenant.
SYSTEM_ROLE = "system"
# user_id namespace of the automation principal ("system:<actor>"). Reserved:
# services.auth refuses any HTTP caller that presents an id in this namespace.
SYSTEM_USER_PREFIX = "system:"


_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "ta_principal", default=None
)


def _default_principal() -> Principal:
    return Principal(
        tenant_id=settings.default_tenant_id,
        user_id=settings.default_user_id,
        role="admin",
    )


def set_principal(principal: Principal) -> contextvars.Token:
    return _principal.set(principal)


def reset_principal(token: contextvars.Token) -> None:
    _principal.reset(token)


def get_principal() -> Principal:
    return _principal.get() or _default_principal()


def is_human_principal() -> bool:
    """True when a *human* principal is explicitly bound to this context.

    A principal is bound by the request dependency (an authenticated human is
    driving the call) or by use_principal/use_tenant (an operator/test). It is
    NOT bound in unattended contexts — agent runs, ad-hoc scripts — which fall
    back to the default; and scheduled jobs bind the non-human SYSTEM_ROLE via
    use_system(). The rejection guard uses this so a solely automated context
    can never reject a candidate, whether or not it bound a tenant.
    """
    p = _principal.get()
    return p is not None and p.role != SYSTEM_ROLE


def is_system_principal() -> bool:
    """True inside use_system(): unattended automation acting for one tenant."""
    p = _principal.get()
    return p is not None and p.role == SYSTEM_ROLE


def get_tenant_id() -> str:
    return get_principal().tenant_id


def current_user_id() -> str:
    return get_principal().user_id


def current_role() -> str:
    return get_principal().role


@contextmanager
def use_principal(principal: Principal):
    """Bind a principal for the duration of the block (tests, background jobs)."""
    token = _principal.set(principal)
    try:
        yield principal
    finally:
        _principal.reset(token)


@contextmanager
def use_tenant(tenant_id: str, *, role: str = "admin", user_id: str | None = None):
    """Convenience: bind just a tenant as a *person* (admin role by default) —
    for operator-driven export/delete and for tests. Not for scheduled jobs:
    see use_system()."""
    with use_principal(
        Principal(tenant_id=tenant_id, user_id=user_id or settings.default_user_id, role=role)
    ) as p:
        yield p


@contextmanager
def use_system(tenant_id: str, *, actor: str = "scheduler"):
    """Bind the least-privilege automation principal for one tenant.

    For Celery/cron work that runs with no human present. Scopes every
    repository read/write to `tenant_id` exactly like a request would, but:
    role is SYSTEM_ROLE (no RBAC permissions), is_human_principal() is False
    (the rejection gate holds), and audit rows carry actor_type='system' with
    actor_id='system:<actor>' so they are never attributed to a real user.
    """
    with use_principal(
        Principal(tenant_id=tenant_id, user_id=f"{SYSTEM_USER_PREFIX}{actor}", role=SYSTEM_ROLE)
    ) as p:
        yield p
