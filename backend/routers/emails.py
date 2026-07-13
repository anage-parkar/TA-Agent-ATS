"""Decision emails + reply tracking.

Flow:
  Proceed/Reject  → draft-email (AI)  → human edits → send-email (Gmail + store)
  proceed send    → application stage = 'Outreach'
  poll-replies    → match reply to thread → classify intent →
                    interested reply → application stage = 'Replied' (auto)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from services.rbac import require_permission
from agents.outreach import draft_decision_email, template_decision_email
from agents.response_parser import classify_reply
from db import repository
from services import email_service, events
from services.config import settings
from services.llm_client import LLMError
from services.tenant_context import current_user_id

logger = logging.getLogger("ta_agent.routers.emails")

router = APIRouter(prefix="/api", tags=["emails"])


def _now():
    return datetime.now(timezone.utc).isoformat()


@router.post("/applications/{application_id}/draft-email", dependencies=[Depends(require_permission("email.send"))])
def draft_email(
    application_id: str,
    decision: str = Body(..., embed=True),
    use_ai: bool = Body(False, embed=True),
):
    """Draft a proceed/reject email for review (does NOT send).

    Default: an instant template (no LLM) so the click is immediate.
    use_ai=true: a richer, personalised draft via the LLM (slower).
    """
    if decision not in ("proceed", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'proceed' or 'reject'")
    detail = repository.get_application_detail(application_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Application not found")
    candidate = detail.get("candidate") or {}
    job_title = detail.get("job_title") or ""

    if use_ai:
        try:
            draft = draft_decision_email(decision, candidate, job_title)
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        draft = template_decision_email(decision, candidate, job_title)

    return {"decision": decision, "to": candidate.get("email"), **draft.model_dump()}


@router.post("/applications/{application_id}/send-email", dependencies=[Depends(require_permission("email.send"))])
def send_email(
    application_id: str,
    decision: str = Body(...),
    subject: str = Body(...),
    body: str = Body(...),
):
    """Apply the recruiter decision and send the (human-approved) email."""
    if decision not in ("proceed", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'proceed' or 'reject'")
    detail = repository.get_application_detail(application_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Application not found")
    candidate_id = detail.get("candidate_id") or (detail.get("candidate") or {}).get("id")
    candidate = repository.get_candidate(candidate_id) if candidate_id else None
    to = (candidate or detail.get("candidate") or {}).get("email")
    if not to:
        raise HTTPException(status_code=400, detail="Candidate has no email address.")

    # Consent-aware: never send to an opted-out candidate (GDPR).
    if candidate and candidate.get("opted_out"):
        raise HTTPException(
            status_code=409, detail="Candidate has opted out of communications; not sending."
        )

    # Every outreach message carries a GDPR opt-out link.
    opt_out_url = f"{settings.public_base_url}/api/applications/{application_id}/opt-out"
    body_out = body if opt_out_url in body else f"{body}\n\nTo opt out of these emails: {opt_out_url}"

    # Record the recruiter decision (human-gated; reject requires a human actor).
    status = "approved" if decision == "proceed" else "rejected"
    fields = {"status": status, "recruiter_decision": decision}
    if decision == "proceed":
        fields["stage"] = "Outreach"
    repository.update_application(application_id, fields)

    # Send (or degrade to stored draft if email isn't configured yet).
    ok, reason = email_service.availability()
    thread_id = None
    sent = False
    if ok:
        try:
            res = email_service.send_email(to, subject, body_out, decision=decision, opt_out_url=opt_out_url)
            thread_id = res["thread_id"]
            sent = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Email send failed")
            raise HTTPException(status_code=502, detail=f"Email send failed: {exc}") from exc

    repository.create_email(
        {
            "application_id": application_id,
            "direction": "outbound",
            "subject": subject,
            "body": body_out,
            "sent_at": _now() if sent else None,
            "thread_id": thread_id,
            "intent": decision,
        }
    )
    # Log to the candidate timeline with the acting human recorded.
    events.emit_event(
        "outreach.sent",
        entity_type="application", entity_id=application_id,
        actor_type="human", actor_id=current_user_id(),
        metadata={"decision": decision, "to": to, "sent": sent, "opt_out_link": opt_out_url},
    )
    return {
        "ok": True,
        "sent": sent,
        "status": status,
        "thread_id": thread_id,
        "opt_out_url": opt_out_url,
        "detail": None if sent else reason,
    }


@router.post("/emails/poll-replies", dependencies=[Depends(require_permission("email.send"))])
def poll_replies():
    """Pull replies, classify intent, auto-advance interested ones to 'Replied'."""
    ok, reason = email_service.availability()
    if not ok:
        return {"ok": False, "detail": reason}

    threads = repository.list_outbound_threads()
    by_thread = {t["thread_id"]: t["application_id"] for t in threads if t.get("thread_id")}
    try:
        replies = email_service.list_replies(set(by_thread.keys()))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reply poll failed")
        raise HTTPException(status_code=502, detail=f"Reply poll failed: {exc}") from exc

    advanced, recorded = 0, 0
    for r in replies:
        tid = r["thread_id"]
        if repository.reply_already_recorded(tid):
            continue
        app_id = by_thread.get(tid)
        if not app_id:
            continue
        try:
            intent = classify_reply(r["body"])
        except LLMError:
            continue
        repository.create_email(
            {
                "application_id": app_id,
                "direction": "inbound",
                "subject": "(reply)",
                "body": r["body"],
                "replied_at": _now(),
                "intent": intent.intent,
                "raw_reply": r["body"],
                "thread_id": tid,
            }
        )
        recorded += 1
        if intent.intent == "interested":
            repository.update_application(app_id, {"stage": "Replied"})
            advanced += 1

    return {"ok": True, "replies": recorded, "advanced_to_replied": advanced}
