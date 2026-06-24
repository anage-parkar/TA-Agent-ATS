"""Request-scoped tenant + identity context (Workstream A).

A `Principal` (tenant_id, user_id, role) is bound per request via a contextvar
set in an app-level dependency (see services/auth.py). The repository reads
`get_tenant_id()` to scope the in-memory store and to set the Postgres
`app.tenant_id` GUC that RLS policies key off — so tenant isolation does not
depend on every query remembering a WHERE clause.

Outside a request (Celery tasks, pollers, scripts) nothing is bound, so
`get_principal()` falls back to the seeded default org/admin. Background jobs
that act for a specific tenant should wrap their work in `use_principal(...)`.
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
    """Convenience: bind just a tenant (admin role) — for export/delete/cron."""
    with use_principal(
        Principal(tenant_id=tenant_id, user_id=user_id or settings.default_user_id, role=role)
    ) as p:
        yield p
