"""Scheduled tasks: reply polling and retention enforcement, for every tenant.

Both run outside a request, so no principal is bound by services.tenant_context
by default (see its docstring). Each task lists all tenants from the
control-plane `organizations` table and explicitly binds one at a time via
`use_system` — the least-privilege automation principal: it scopes the tenant
(in-memory filter / Postgres RLS) exactly like a request would, but holds no
RBAC permissions, can never satisfy the human-actor gate on rejection, and is
audited as actor_type='system' / actor_id='system:scheduler'. Never bind an
admin here: a tenant-binding bug under an admin principal would mis-attribute
automated changes to a real user and could act with that user's permissions.

A failure for one tenant is logged and skipped; it doesn't stop the others.
"""

from __future__ import annotations

import logging

from db import repository
from services import retention
from services.reply_poller import poll_replies
from services.tenant_context import use_system
from tasks.celery_app import celery_app

logger = logging.getLogger("ta_agent.tasks")


@celery_app.task(name="tasks.jobs.poll_replies_all_tenants")
def poll_replies_all_tenants() -> dict:
    results: dict[str, dict] = {}
    for org in repository.list_organizations():
        tenant_id = org["id"]
        try:
            with use_system(tenant_id):
                results[tenant_id] = poll_replies()
        except Exception:  # noqa: BLE001 — one tenant's failure shouldn't stop the rest
            logger.exception("poll_replies failed for tenant %s", tenant_id)
            results[tenant_id] = {"ok": False, "detail": "error — see worker log"}
    return results


@celery_app.task(name="tasks.jobs.enforce_retention_all_tenants")
def enforce_retention_all_tenants() -> dict:
    results: dict[str, dict] = {}
    for org in repository.list_organizations():
        tenant_id = org["id"]
        try:
            with use_system(tenant_id):
                results[tenant_id] = retention.enforce(tenant_id)
        except Exception:  # noqa: BLE001
            logger.exception("retention.enforce failed for tenant %s", tenant_id)
            results[tenant_id] = {"status": "error"}
    return results
