"""Public legal / vendor-posture endpoints (Workstream F)."""

from __future__ import annotations

from fastapi import APIRouter

from services import compliance

router = APIRouter(prefix="/api/legal", tags=["legal"])


@router.get("/subprocessors")
def subprocessors():
    """Published sub-processor list (transparency for customers/candidates)."""
    return {"subprocessors": compliance.SUBPROCESSORS}
