"""Graph state schema (Phase 2 / PR 1) — references, never PII.

Everything a checkpoint persists is described here, and every field is an
identifier, an enum, a number, a timestamp or a boolean. The redacted profile,
evidence text, draft emails and replies live in the repository and are loaded
by id inside nodes; interrupt payloads shown to the UI are assembled at read
time. Consequences: checkpoint erasure is defence in depth, version migrations
stay small, checkpoints stay tiny.

Two guarantees, enforced by tests/test_phase2_storage.py:

* **structural** — the schema is walked and any string field not marked as an
  identifier (``IdMarker`` *and* the ``ID_PATTERN`` constraint) fails;
* **runtime** — an id-marked field validates its format on every assignment:
  a UUID, or one of our prefixed ids (``system:scheduler``, ``evt:…``,
  ``idem:<uuid>:send_gate:<event>``). A name or any free text is rejected by
  pydantic, so marking a field as an id cannot smuggle prose through.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class IdMarker:
    """Field metadata: this string is an opaque identifier, never free text."""


UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# Prefixed ids: a lowercase namespace, a colon, then 1–5 colon-separated tokens
# of [A-Za-z0-9_-.] — no whitespace, no '@', so neither prose nor an email fits.
PREFIXED_ID_RE = r"[a-z][a-z0-9_]{0,15}:[A-Za-z0-9_\-.]{1,64}(?::[A-Za-z0-9_\-.]{1,64}){0,4}"
ID_PATTERN = rf"^(?:{UUID_RE}|{PREFIXED_ID_RE})$"
ID_RE = re.compile(ID_PATTERN)


def is_id(value: object) -> bool:
    """True when `value` has one of the accepted identifier formats."""
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


IdStr = Annotated[str, IdMarker(), Field(pattern=ID_PATTERN)]

# Every type a checkpoint may contain; registered with the serializer.
__all__ = ["Gate", "Outcome", "ThreadStatus", "SendStatus", "ScoreSummary",
           "Decision", "OutreachRef", "InterviewRef", "ThreadState"]


class Gate(str, Enum):
    review = "review_gate"
    send = "send_gate"
    interview = "interview_gate"


class Outcome(str, Enum):
    proceed = "proceed"
    reject = "reject"
    approve = "approve"
    confirm = "confirm"


class ThreadStatus(str, Enum):
    running = "running"
    waiting_human = "waiting_human"
    waiting_event = "waiting_event"
    closed = "closed"
    stranded = "stranded"          # no migration path after a deploy (§4 of the design note)


class SendStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    unknown = "unknown"            # ambiguous Gmail error; resolved by Message-ID lookup (PR 3)
    failed = "failed"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ScoreSummary(_Strict):
    overall: float
    passed_knockouts: bool
    below_threshold: bool


class Decision(_Strict):
    gate: Gate
    outcome: Outcome
    by: IdStr                      # the RESUMING principal's user id (audit context only)
    at: datetime
    event_id: IdStr


class OutreachRef(_Strict):
    email_id: IdStr | None = None
    idempotency_key: IdStr | None = None   # "idem:<application uuid>:<gate>:<event id>" (PR 3)
    send_status: SendStatus | None = None


class InterviewRef(_Strict):
    interview_id: IdStr | None = None


class ThreadState(_Strict):
    application_id: IdStr
    candidate_id: IdStr
    job_id: IdStr
    tenant_id: IdStr
    graph_version: int
    started_by: IdStr              # context only — never authority (design note §2)
    status: ThreadStatus
    orchestrated: bool             # rollout flag, frozen at thread start (§6)
    last_event_id: IdStr | None = None
    score_summary: ScoreSummary | None = None
    decisions: list[Decision] = Field(default_factory=list)
    outreach: OutreachRef = Field(default_factory=OutreachRef)
    interview: InterviewRef = Field(default_factory=InterviewRef)
