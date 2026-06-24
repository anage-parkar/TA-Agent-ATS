"""Tenant administration — create tenants, export and purge tenant data.

All routes are admin-only (RBAC `tenant.admin`). Export and delete are
tenant-scoped: they bind the target tenant so RLS (or the in-memory filter)
returns/removes only that tenant's rows — supporting per-tenant portability
and right-to-erasure (Workstream F builds on this).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from db import repository
from services.rbac import require_permission

logger = logging.getLogger("ta_agent.routers.admin")

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_permission("tenant.admin"))],
)


@router.post("/tenants")
def create_tenant(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    org = repository.create_organization(
        name=name, slug=payload.get("slug"), region=payload.get("region", "global")
    )
    return {"tenant": org}


@router.get("/tenants/{tenant_id}/export")
def export_tenant(tenant_id: str):
    """Full export of a tenant's data (portability / audit)."""
    data = repository.export_tenant(tenant_id)
    counts = {k: len(v) for k, v in data.items()}
    return {"tenant_id": tenant_id, "counts": counts, "data": data}


@router.delete("/tenants/{tenant_id}")
def delete_tenant(tenant_id: str):
    """Purge all domain data for a tenant (right-to-erasure)."""
    deleted = repository.delete_tenant(tenant_id)
    logger.info("purged tenant %s: %s", tenant_id, deleted)
    return {"tenant_id": tenant_id, "deleted": deleted}
