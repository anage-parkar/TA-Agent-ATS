"""Data retention enforcement (Workstream F).

Deletes candidate data older than the tenant's configured retention window
(`organizations.retention_days`; null = retain indefinitely). Designed to be run
on a schedule (Celery beat / cron) per tenant; also exposed via an admin
endpoint for on-demand runs. Each erasure is audited and tenant-scoped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db import repository


def enforce(tenant_id: str | None = None) -> dict:
    """Erase candidates created before the tenant's retention cutoff."""
    org = repository.get_organization(tenant_id) or {}
    days = org.get("retention_days")
    if not days or int(days) <= 0:
        return {"status": "disabled", "retention_days": days}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    result = repository.delete_candidates_older_than(cutoff)
    return {"status": "ok", "retention_days": int(days), "cutoff": cutoff, **result}
