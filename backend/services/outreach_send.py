"""Ledger-first outreach send (Phase 2 / PR 3).

The graph's send gate never calls Gmail without a committed ledger row:

1. **Resolve first.** Any earlier attempt for the application still `pending`
   or `unknown` is looked up in the mailbox — by the deterministic
   `Message-ID` we set, then by the `X-TA-Idempotency-Key` header on recent
   sent mail to the recipient — and settled to `sent` or `failed`. A
   re-approval can never mint a new key while an attempt is unresolved, and if
   the earlier attempt turns out to have gone out, nothing is sent again.
2. **Record, then send.** A new attempt writes the `emails` row (status
   `pending`, the idempotency key, the Message-ID) and only then calls Gmail
   with those headers. Success → `sent`. An **ambiguous** failure (timeout,
   5xx) → `unknown`, never `failed`: the message may have gone out. A
   **definite** failure (4xx) → `failed`: safe to resend under the same key.
3. **Same key, same outcome.** Sending with a key that is already `sent`
   returns the existing row without touching Gmail.

The legacy route (routers/emails.send_email) is untouched and does not use
the ledger.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from db import repository
from services import email_service
from services.config import settings

logger = logging.getLogger("ta_agent.outreach_send")

UNRESOLVED = ("pending", "unknown")

# Gmail search indexing can lag, so a negative lookup shortly after an unknown
# attempt is NOT proof the email was not sent. A negative result may settle an
# attempt as failed — and so allow a new key — only when the attempt is at
# least MIN_UNKNOWN_AGE_SECONDS old AND the lookup has been retried at least
# twice (LOOKUP_ATTEMPTS lookups in total, spaced by LOOKUP_RETRY_DELAY_SECONDS).
MIN_UNKNOWN_AGE_SECONDS = 300
MIN_LOOKUPS_BEFORE_FAILED = 3     # initial lookup + at least two retries, across calls
LOOKUP_ATTEMPTS = 3               # lookups performed per resolution pass (stops early when found)
LOOKUP_RETRY_DELAY_SECONDS = 2.0


class OutreachRefused(RuntimeError):
    """The send must not happen at all (opted out, no address, not configured)."""


class OutreachUnresolved(OutreachRefused):
    """An earlier attempt is still unresolved and too young / too little
    retried for a negative lookup to count. No new key may be minted yet."""


@dataclass
class SendResult:
    email: dict[str, Any]
    action: str  # sent | already_sent | resolved_sent | unknown | failed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def idempotency_key(application_id: str, gate: str, event_id: str) -> str:
    """`idem:<application>:<gate>:<approval event>` — prefixed so it validates
    as an id in graph state (orchestrator.state.ID_PATTERN)."""
    return f"idem:{application_id}:{gate}:{event_id}"


def sender_domain() -> str:
    addr = (settings.outreach_from_email or "").strip()
    return addr.split("@", 1)[1] if "@" in addr else "ta-agent.invalid"


def deterministic_message_id(key: str, domain: str | None = None) -> str:
    """The RFC 5322 Message-ID for an attempt is a pure function of its key,
    so a retry of the same attempt carries the same id and a mailbox lookup
    by `rfc822msgid:` finds it."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return f"<ta-{digest}@{domain or sender_domain()}>"


def prepare(application_id: str, body: str) -> tuple[str, str, str]:
    """Recipient, body with the GDPR opt-out link, and the opt-out URL — the
    same consent rules as the legacy route. Raises OutreachRefused."""
    detail = repository.get_application_detail(application_id)
    if not detail:
        raise OutreachRefused("Application not found")
    candidate_id = detail.get("candidate_id") or (detail.get("candidate") or {}).get("id")
    candidate = repository.get_candidate(candidate_id) if candidate_id else None
    to = ((candidate or detail.get("candidate") or {}).get("email") or "").strip()
    if not to:
        raise OutreachRefused("Candidate has no email address.")
    if candidate and candidate.get("opted_out"):
        raise OutreachRefused("Candidate has opted out of communications; not sending.")
    opt_out_url = f"{settings.public_base_url}/api/applications/{application_id}/opt-out"
    body_out = body if opt_out_url in body else f"{body}\n\nTo opt out of these emails: {opt_out_url}"
    return to, body_out, opt_out_url


def _age_seconds(row: dict[str, Any]) -> float:
    created = row.get("created_at")
    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if created is None:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds()


def _lookup_with_retries(row: dict[str, Any], to: str) -> tuple[dict[str, Any] | None, int]:
    """Look the attempt up LOOKUP_ATTEMPTS times (stop early when found).
    Returns (found, lookups_performed)."""
    found, performed = None, 0
    for attempt in range(LOOKUP_ATTEMPTS):
        found = email_service.find_sent_message(
            message_id=row.get("message_id"), idempotency_key=row.get("idempotency_key"), to=to)
        performed += 1
        if found:
            break
        if attempt < LOOKUP_ATTEMPTS - 1 and LOOKUP_RETRY_DELAY_SECONDS > 0:
            time.sleep(LOOKUP_RETRY_DELAY_SECONDS)
    return found, performed


def _resolve(row: dict[str, Any], to: str, *, reason: str) -> dict[str, Any]:
    """One resolution pass over an unresolved attempt. Returns the row:
    `sent` when found; `failed` when not found AND old enough AND retried
    enough; otherwise still `unknown` (nothing may be minted yet)."""
    found, performed = _lookup_with_retries(row, to)
    attempts = int(row.get("resolve_attempts") or 0) + performed
    row = repository.update_email(row["id"], {"resolve_attempts": attempts, "last_lookup_at": _now()})
    if found:
        return repository.update_email(row["id"], {
            "send_status": "sent",
            "sent_at": row.get("sent_at") or _now(),
            "thread_id": row.get("thread_id") or found.get("thread_id"),
            "provider_message_id": found.get("message_id"),
            "last_error": None,
        })
    age = _age_seconds(row)
    if age >= MIN_UNKNOWN_AGE_SECONDS and attempts >= MIN_LOOKUPS_BEFORE_FAILED:
        return repository.update_email(row["id"], {
            "send_status": "failed",
            "last_error": f"not found in mailbox after {attempts} lookups over {int(age)}s ({reason})",
        })
    return repository.update_email(row["id"], {
        "send_status": "unknown",
        "last_error": f"unresolved: {attempts} lookup(s), age {int(age)}s < {MIN_UNKNOWN_AGE_SECONDS}s ({reason})",
    })


def resolve_unresolved(application_id: str, to: str) -> list[dict[str, Any]]:
    """Settle every pending/unknown attempt for the application by looking it
    up in the mailbox. Runs BEFORE any new key may be minted. Raises
    OutreachUnresolved when an attempt is still unknown afterwards (too young
    or too little retried for a negative result to count)."""
    settled = []
    for row in repository.list_unresolved_sends(application_id):
        settled.append(_resolve(row, to, reason="resolution before re-approval"))
    still = [r for r in settled if r.get("send_status") in UNRESOLVED]
    if still:
        r = still[0]
        raise OutreachUnresolved(
            f"earlier send attempt {r.get('idempotency_key')} is unresolved "
            f"({r.get('last_error')}); retry after it is {MIN_UNKNOWN_AGE_SECONDS // 60} minutes old"
        )
    return settled


def send_with_ledger(*, application_id: str, to: str, subject: str, body: str, key: str,
                     decision: str = "proceed", opt_out_url: str | None = None) -> SendResult:
    """Record the attempt under `key`, then send. Idempotent on the key."""
    existing = repository.get_email_by_idempotency_key(key)
    if existing and existing.get("send_status") == "sent":
        return SendResult(existing, "already_sent")
    if existing and existing.get("send_status") in UNRESOLVED:
        row = _resolve(existing, to, reason="retry of an unresolved attempt")
        if row.get("send_status") == "sent":
            return SendResult(row, "resolved_sent")
        if row.get("send_status") in UNRESOLVED:
            raise OutreachUnresolved(
                f"send attempt {key} is unresolved ({row.get('last_error')}); "
                f"retry after it is {MIN_UNKNOWN_AGE_SECONDS // 60} minutes old"
            )
    elif existing:
        row = existing  # a definite failure: resend under the same key
    else:
        row = repository.create_email({
            "application_id": application_id, "direction": "outbound",
            "subject": subject, "body": body, "intent": decision,
            "idempotency_key": key, "message_id": deterministic_message_id(key),
            "send_status": "pending", "sent_at": None, "thread_id": None,
        })

    # The ledger row is committed; only now do we talk to the provider.
    row = repository.update_email(row["id"], {"send_status": "pending", "last_error": None})
    try:
        res = email_service.send_email(
            to, subject, body, decision=decision, opt_out_url=opt_out_url,
            message_id=row["message_id"], idempotency_key=key,
        )
    except Exception as exc:  # noqa: BLE001 — every failure is classified, never swallowed
        kind = type(exc) if isinstance(exc, email_service.SendError) else email_service.classify_send_error(exc)
        if kind is email_service.DefiniteSendError:
            logger.warning("definite send failure for %s: %s", key, exc)
            return SendResult(repository.update_email(row["id"], {
                "send_status": "failed", "last_error": str(exc)[:500]}), "failed")
        # Ambiguous (timeout, 5xx, anything unproven): the message MAY have gone
        # out. Never 'failed' — the next approval resolves it by mailbox lookup.
        logger.warning("ambiguous send for %s: %s", key, exc)
        return SendResult(repository.update_email(row["id"], {
            "send_status": "unknown", "last_error": str(exc)[:500]}), "unknown")

    return SendResult(repository.update_email(row["id"], {
        "send_status": "sent", "sent_at": _now(), "thread_id": res.get("thread_id"),
        "provider_message_id": res.get("message_id"), "last_error": None,
    }), "sent")


def send_for_application(application_id: str, *, event_id: str, subject: str, body: str,
                         decision: str = "proceed") -> SendResult:
    """The send gate's single mutation: resolve earlier attempts, then — unless
    one of them turns out to have gone out — mint the key for THIS approval
    and send."""
    to, body_out, opt_out_url = prepare(application_id, body)
    for row in resolve_unresolved(application_id, to):
        if row.get("send_status") == "sent":
            return SendResult(row, "resolved_sent")  # the earlier approval's email went out; never twice
    key = idempotency_key(application_id, "send_gate", event_id)
    return send_with_ledger(application_id=application_id, to=to, subject=subject, body=body_out,
                            key=key, decision=decision, opt_out_url=opt_out_url)
