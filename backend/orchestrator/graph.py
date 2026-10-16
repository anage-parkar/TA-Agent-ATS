"""The per-application state machine (Phase 2 / PR 2 — skeleton).

```
ingest ─► score(AI) ─► review_gate ⏸ ─► send_gate ⏸ ─► await_reply ⏳ ─► interview_gate ⏸ ─► scheduled ─► END
                            │ reject
                            └──► close ─► END
```

* Every ⏸ / ⏳ node calls `interrupt()` with a *reference* payload (gate name,
  application id, graph version) and performs **no side effect before it**:
  LangGraph re-runs an interrupted node from the top on resume, so everything
  that writes happens after `interrupt()` returns, as the node's single
  mutation.
* **The interrupt is the flow mechanism; the repository gate is the
  enforcement.** `review_gate` applies a reject through
  `repository.update_application(status="rejected")`, which raises
  `HumanActorRequired` unless a *human* principal is bound at resume time —
  so a Celery task, a replay or a bug that resumes the gate cannot reject.
* Every node runs under the **resuming** principal: the runner puts it in
  `config["configurable"]["principal"]` and `_node` rebinds it with
  `use_principal` inside the node, because contextvars do not cross
  LangGraph's executor threads (design note §2). Nodes never read it from
  state; `started_by` is context only.

PR 2 ships the topology and the gates. `send_gate` records approval only
(PR 3 adds the idempotent send); `await_reply` accepts the event shape PR 4
will deliver from Celery; the scoring port reads the recorded ATS decision
(live scoring through the graph arrives with the rollout PR).
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from db import repository
from db.repository import HumanActorRequired
from orchestrator.checkpointer import get_checkpointer
from orchestrator.state import Decision, Gate, Outcome, ScoreSummary, ThreadState, ThreadStatus
from services import events
from services.tenant_context import Principal, current_user_id, is_human_principal, use_principal

logger = logging.getLogger("ta_agent.orchestrator.graph")

# Bump whenever node names, edges, the state schema or an interrupt-payload
# contract change; see orchestrator/migrations.py for what a bump requires.
GRAPH_VERSION = 1

GATE_NODES = ("review_gate", "send_gate", "interview_gate")
EVENT_NODES = ("await_reply",)


# ── resume binding ───────────────────────────────────────────────────────
class ResumeOriginMismatch(RuntimeError):
    """The resume value parked on the thread was not minted by this call.

    LangGraph persists a Command(resume=...) value on the checkpoint BEFORE the
    node runs, so a value from an earlier call (e.g. a system-planted reject
    whose worker crashed before cleanup) can be handed to a later resume. The
    runner therefore wraps every resume in an envelope carrying the resuming
    principal and a one-time resume id, and a gate acts only on the envelope
    whose principal and id match the current call's. Anything else is refused
    here and cleared by the runner.
    """

    def __init__(self, stale: Any, expected: dict) -> None:
        self.stale_resume_id = stale.get("resume_id") if isinstance(stale, dict) else None
        self.stale_principal = stale.get("principal") if isinstance(stale, dict) else None
        super().__init__(
            f"resume value does not belong to this call: got resume_id={self.stale_resume_id!r} "
            f"principal={self.stale_principal!r}, expected resume_id={expected.get('resume_id')!r} "
            f"principal={expected.get('principal')!r}"
        )


# Set by _node for the duration of a node: what THIS call's resume must match.
_binding: ContextVar[dict | None] = ContextVar("ta_resume_binding", default=None)


def gate_interrupt(ref: dict[str, Any]) -> dict[str, Any]:
    """`interrupt()` plus the origin check. Returns the caller's payload."""
    envelope = interrupt(ref)
    expected = _binding.get() or {}
    if (
        not isinstance(envelope, dict)
        or not expected.get("resume_id")
        or envelope.get("resume_id") != expected["resume_id"]
        or envelope.get("principal") != expected["principal"]
    ):
        raise ResumeOriginMismatch(envelope, expected)
    payload = envelope.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


# ── scoring port ─────────────────────────────────────────────────────────
class ScoringUnavailable(RuntimeError):
    """The application has no recorded ATS decision for the graph to use."""


def score_application(application: dict) -> ScoreSummary:
    """Default scoring port: reuse the application's recorded ATS decision.

    In PR 2 the legacy pipeline still scores before any thread exists, so the
    graph reads what it recorded rather than calling the LLM again. The rollout
    PR injects live scoring through this same port.
    """
    score = application.get("ats_score")
    if score is None:
        raise ScoringUnavailable(
            f"application {application.get('id')} has no recorded ATS score"
        )
    threshold: float | None = None
    for d in reversed(repository.list_ai_decisions_for_application(application["id"])):
        if d.get("kind") == "ats_score":
            threshold = (d.get("scores") or {}).get("threshold")
            break
    threshold = 60.0 if threshold is None else float(threshold)
    breakdown = application.get("ats_breakdown") or {}
    if isinstance(breakdown, str):
        import json

        try:
            breakdown = json.loads(breakdown)
        except ValueError:
            breakdown = {}
    knocked_out = str(breakdown.get("reasoning", "")).startswith("Knocked out")
    return ScoreSummary(
        overall=float(score),
        passed_knockouts=not knocked_out,
        below_threshold=float(score) < threshold,
    )


# ── node plumbing ────────────────────────────────────────────────────────
def _node(fn):
    """Run `fn(state)` under the principal carried in the run config.

    Not `functools.wraps`: LangGraph decides whether to inject `config` by
    inspecting the callable's signature, and `wraps` would hide this one.
    """

    def wrapped(state: ThreadState, config: RunnableConfig) -> dict[str, Any]:
        conf = config.get("configurable") or {}
        raw = conf.get("principal")
        if not raw:
            raise RuntimeError("graph invoked without a principal in config; use orchestrator.runner")
        token = _binding.set({"resume_id": conf.get("resume_id"), "principal": dict(raw)})
        try:
            with use_principal(Principal(**raw)):
                return fn(state)
        finally:
            _binding.reset(token)

    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


def _ref(state: ThreadState, gate: str) -> dict[str, Any]:
    """Interrupt payload: a reference, never content (design note §3)."""
    return {"gate": gate, "application_id": state.application_id, "graph_version": state.graph_version}


def _event_id(resume: dict | None) -> str:
    eid = (resume or {}).get("event_id")
    return eid if isinstance(eid, str) and eid else f"evt:{uuid.uuid4().hex}"


def _decision(gate: Gate, outcome: Outcome, resume: dict | None) -> Decision:
    return Decision(gate=gate, outcome=outcome, by=current_user_id(),
                    at=datetime.now(timezone.utc), event_id=_event_id(resume))


def _require_human(action: str) -> None:
    if not is_human_principal():
        raise HumanActorRequired(f"{action} requires a recorded human actor")


# ── nodes ────────────────────────────────────────────────────────────────
@_node
def ingest(state: ThreadState) -> dict[str, Any]:
    if repository.get_application(state.application_id) is None:
        raise RuntimeError(f"application {state.application_id} not found for this tenant")
    return {"status": ThreadStatus.running}


@_node
def score(state: ThreadState) -> dict[str, Any]:
    application = repository.get_application(state.application_id)
    return {"score_summary": score_application(application)}


@_node
def review_gate(state: ThreadState) -> dict[str, Any]:
    """Human decision: proceed or reject. No side effects before the interrupt."""
    resume = gate_interrupt(_ref(state, "review_gate"))
    decision = resume.get("decision")
    if decision not in ("proceed", "reject"):
        raise ValueError("review_gate resume requires decision 'proceed' or 'reject'")
    fields: dict[str, Any] = {
        "status": "approved" if decision == "proceed" else "rejected",
        "recruiter_decision": decision,
    }
    if decision == "proceed":
        fields["stage"] = "Reviewed"
    # The single mutation. A reject raises HumanActorRequired unless the
    # RESUMING principal is human — the repository gate, not the graph, decides.
    repository.update_application(state.application_id, fields)
    outcome = Outcome.proceed if decision == "proceed" else Outcome.reject
    return {
        "decisions": [*state.decisions, _decision(Gate.review, outcome, resume)],
        "status": ThreadStatus.running if decision == "proceed" else ThreadStatus.closed,
    }


def _after_review(state: ThreadState) -> str:
    return "close" if state.status == ThreadStatus.closed else "send_gate"


@_node
def send_gate(state: ThreadState) -> dict[str, Any]:
    """Human approval to send outreach. PR 2 records the approval only."""
    resume = gate_interrupt(_ref(state, "send_gate"))
    if not resume.get("approve"):
        raise ValueError("send_gate resume requires approve: true")
    _require_human("approving outreach")
    decision = _decision(Gate.send, Outcome.approve, resume)
    events.emit_event(
        "outreach.approved", entity_type="application", entity_id=state.application_id,
        actor_type="human", actor_id=current_user_id(),
        metadata={"event_id": decision.event_id, "gate": "send_gate"},
    )
    return {"decisions": [*state.decisions, decision], "status": ThreadStatus.running}


@_node
def await_reply(state: ThreadState) -> dict[str, Any]:
    """External wait: a typed event delivered by Celery (PR 4) resumes it."""
    event = gate_interrupt({"wait": "reply.received", "application_id": state.application_id,
                            "graph_version": state.graph_version})
    if event.get("event") != "reply.received":
        raise ValueError("await_reply resume requires event 'reply.received'")
    event_id = _event_id(event)
    events.emit_event(
        "orchestrator.event_received", entity_type="application", entity_id=state.application_id,
        actor_type="human" if is_human_principal() else "system", actor_id=current_user_id(),
        metadata={"event": "reply.received", "event_id": event_id},
    )
    return {"last_event_id": event_id, "status": ThreadStatus.running}


@_node
def interview_gate(state: ThreadState) -> dict[str, Any]:
    """Human confirmation of an interview slot. PR 2 records the decision only."""
    resume = gate_interrupt(_ref(state, "interview_gate"))
    if not resume.get("confirm"):
        raise ValueError("interview_gate resume requires confirm: true")
    _require_human("confirming an interview")
    return {"decisions": [*state.decisions, _decision(Gate.interview, Outcome.confirm, resume)],
            "status": ThreadStatus.running}


@_node
def scheduled(state: ThreadState) -> dict[str, Any]:
    return {"status": ThreadStatus.closed}


@_node
def close(state: ThreadState) -> dict[str, Any]:
    return {"status": ThreadStatus.closed}


# ── graph ────────────────────────────────────────────────────────────────
def build_graph():
    g = StateGraph(ThreadState)
    for name, fn in (("ingest", ingest), ("score", score), ("review_gate", review_gate),
                     ("send_gate", send_gate), ("await_reply", await_reply),
                     ("interview_gate", interview_gate), ("scheduled", scheduled), ("close", close)):
        g.add_node(name, fn)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "score")
    g.add_edge("score", "review_gate")
    g.add_conditional_edges("review_gate", _after_review, {"close": "close", "send_gate": "send_gate"})
    g.add_edge("send_gate", "await_reply")
    g.add_edge("await_reply", "interview_gate")
    g.add_edge("interview_gate", "scheduled")
    g.add_edge("scheduled", END)
    g.add_edge("close", END)
    return g.compile(checkpointer=get_checkpointer())


_compiled = None


def get_graph():
    """The compiled graph bound to the configured checkpointer (built lazily)."""
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def reset_graph() -> None:
    """Drop the compiled graph (tests, or after resetting the checkpointer)."""
    global _compiled
    _compiled = None
