"""Reply-polling orchestration — shared by the on-demand API route
(`POST /api/emails/poll-replies`) and the scheduled Celery task
(`tasks.jobs.poll_replies_all_tenants`).

Runs for whichever tenant is currently bound (services.tenant_context): the
request principal on the API route, or the non-human system principal that the
Celery task binds per tenant via `use_system`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from agents.response_parser import classify_reply
from db import repository
from services import email_service
from services.llm_client import LLMError

logger = logging.getLogger("ta_agent.services.reply_poller")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def poll_replies() -> dict:
    """Pull replies, classify intent, auto-advance interested ones to 'Replied'.

    Returns {"ok": False, "detail": ...} if email isn't configured for this
    tenant. Propagates on a hard failure talking to the mailbox (caller
    decides how to surface/log that).
    """
    ok, reason = email_service.availability()
    if not ok:
        return {"ok": False, "detail": reason}

    threads = repository.list_outbound_threads()
    by_thread = {t["thread_id"]: t["application_id"] for t in threads if t.get("thread_id")}
    replies = email_service.list_replies(set(by_thread.keys()))

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
            logger.exception("Reply classification failed for thread %s", tid)
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
