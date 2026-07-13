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
from services import bias, retention
from services.rbac import require_permission
from services.tenant_context import use_tenant

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


@router.post("/tenants/{tenant_id}/config")
def set_tenant_config(tenant_id: str, payload: dict = Body(...)):
    """Set per-tenant compliance config: retention_days, ai_disclosure,
    privacy_notice_url, region (data residency)."""
    org = repository.update_organization(tenant_id, payload)
    return {"tenant": org}


@router.get("/tenants/{tenant_id}/adverse-impact")
def adverse_impact(tenant_id: str, min_group: int = 5):
    """Selection-rate / four-fifths bias report for a tenant (aggregate only)."""
    with use_tenant(tenant_id):
        return bias.adverse_impact(min_group=min_group)


@router.post("/tenants/{tenant_id}/retention/run")
def run_retention(tenant_id: str):
    """Enforce the tenant's retention policy now (also run on a schedule)."""
    with use_tenant(tenant_id):
        return retention.enforce(tenant_id)


@router.get("/candidates/{candidate_id}/export")
def export_candidate(candidate_id: str):
    """DSAR / portability: everything held about one candidate (tenant-scoped)."""
    data = repository.export_candidate(candidate_id)
    if not data:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return {"candidate_id": candidate_id, "data": data}


@router.delete("/candidates/{candidate_id}")
def erase_candidate(candidate_id: str):
    """Right-to-erasure for one candidate (tenant-scoped; audit retained)."""
    deleted = repository.erase_candidate(candidate_id)
    return {"candidate_id": candidate_id, "deleted": deleted}
