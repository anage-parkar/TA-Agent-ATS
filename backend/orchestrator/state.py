"""Graph state schema (Phase 2 / PR 1) — references, never PII.

Everything a checkpoint persists is described here, and every field is an
identifier, an enum, a number, a timestamp or a boolean. The redacted profile,
evidence text, draft emails and replies live in the repository and are loaded
by id inside nodes; interrupt payloads shown to the UI are assembled at read
time. Consequences: checkpoint erasure is defence in depth, version migrations
stay small, checkpoints stay tiny.

The structural guarantee is enforced by tests/test_phase2_storage.py, which
walks this schema and fails on any free-text string field: a string is allowed
only when annotated with ``IdMarker`` (see ``IdStr``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class IdMarker:
    """Field metadata: this string is an opaque identifier, never free text."""


# Identifiers: UUIDs, "system:<actor>" user ids, event ids. No whitespace, so
# no prose can be smuggled in.
IdStr = Annotated[str, IdMarker(), Field(pattern=r"^[A-Za-z0-9:_\-.]{1,128}$")]


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
    model_config = ConfigDict(extra="forbid")


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
    idempotency_key: IdStr | None = None
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
