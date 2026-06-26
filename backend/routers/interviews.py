"""Interview scheduling router (Workstream G).

Proposing slots is a read; CONFIRMING a slot is a human action (RBAC
`interview.schedule`) — the human-in-the-loop gate is preserved. Every
confirmed interview is recorded and logged to the candidate timeline.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from db import repository
from services import events, scheduling
from services.rbac import require_permission
from services.tenant_context import current_user_id

logger = logging.getLogger("ta_agent.routers.interviews")

router = APIRouter(prefix="/api/applications", tags=["interviews"])


@router.get("/{application_id}/interview/slots")
def interview_slots(application_id: str, interviewer_email: str | None = None,
                    days: int = 5, duration_minutes: int = 60):
    """Propose free/busy-aware interview slots (read-only)."""
    if not repository.get_application_detail(application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    return scheduling.propose_slots(interviewer_email, days=days, duration_minutes=duration_minutes)


@router.post(
    "/{application_id}/interview/confirm",
    dependencies=[Depends(require_permission("interview.schedule"))],
)
def confirm_interview(application_id: str, payload: dict = Body(...)):
    """Human confirms a slot → create the invite + record the interview + log it."""
    detail = repository.get_application_detail(application_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Application not found")
    start_iso = payload.get("scheduled_at")
    if not start_iso:
        raise HTTPException(status_code=400, detail="scheduled_at is required")

    candidate = detail.get("candidate") or {}
    duration = int(payload.get("duration_minutes", 60))
    invite = scheduling.confirm_slot(
        start_iso=start_iso, duration_minutes=duration,
        summary=payload.get("summary", "Interview"),
        interviewer_email=payload.get("interviewer_email"),
        attendee_email=candidate.get("email"),
    )

    interview = repository.create_interview({
        "application_id": application_id,
        "stage": payload.get("stage", "Technical"),
        "scheduled_at": start_iso,
        "duration_minutes": duration,
        "format": payload.get("format", "Video"),
        "interviewer_email": payload.get("interviewer_email"),
        "calendar_event_id": invite.get("calendar_event_id"),
        "confirmation_status": invite.get("confirmation_status", "pending"),
    })
    repository.update_application(application_id, {"stage": "Interview"})
    events.emit_event(
        "interview.scheduled",
        entity_type="application", entity_id=application_id,
        actor_type="human", actor_id=current_user_id(),
        metadata={"scheduled_at": start_iso, "status": invite.get("confirmation_status"),
                  "calendar_event_id": invite.get("calendar_event_id")},
    )
    return {"ok": True, "interview": interview, "calendar": invite}
