"""Start, resume and inspect per-application graph threads (Phase 2 / PR 2).

The runner is the only way into the graph. It:

* **requires an explicitly bound principal** — the request's human principal
  or `use_system(...)`; the default fallback is refused, never defaulted;
* carries that principal into the run config so every node rebinds it (the
  *resuming* principal is what the repository gate and the audit rows see);
* refuses to touch a thread whose state belongs to another tenant (belt; RLS
  on the checkpoint tables is braces);
* migrates a thread lazily on resume when the graph version moved, or strands
  it into human review when no migration path exists (design note §4);
* records `orchestrator.reject_refused` when a non-human resume hits the
  repository's human-actor gate, and re-raises so the caller sees the refusal.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command
from pydantic import BaseModel

from db import repository
from db.repository import HumanActorRequired
from orchestrator import graph as graph_mod
from orchestrator import migrations
from orchestrator.state import ThreadState, ThreadStatus
from services import events, tenant_context
from services.tenant_context import Principal, is_human_principal

logger = logging.getLogger("ta_agent.orchestrator.runner")


class OrchestratorError(RuntimeError):
    """Base for runner refusals (mapped to HTTP 409 by the routers)."""


class NoPrincipalBound(OrchestratorError):
    """A graph run was attempted with no explicitly bound principal."""


class TenantMismatch(OrchestratorError):
    """The bound principal's tenant does not own this thread."""


class NotOrchestrated(OrchestratorError):
    """The application is not on the orchestrated path (rollout flag off)."""


class ThreadStranded(OrchestratorError):
    """The thread's graph version has no migration path; it is in human review."""


# node name -> status while the thread waits there
_WAITING = {**{g: ThreadStatus.waiting_human.value for g in graph_mod.GATE_NODES},
            **{e: ThreadStatus.waiting_event.value for e in graph_mod.EVENT_NODES}}


def _principal() -> Principal:
    p = tenant_context._principal.get()  # the bound one — never get_principal()'s fallback
    if p is None:
        raise NoPrincipalBound(
            "graph runs require an explicitly bound principal (a request, use_system(), or use_tenant())"
        )
    return p


def _config(application_id: str, p: Principal) -> dict[str, Any]:
    return {"configurable": {
        "thread_id": application_id,
        "checkpoint_ns": "",
        "principal": {"tenant_id": p.tenant_id, "user_id": p.user_id, "role": p.role},
    }}


def _values(snapshot) -> dict[str, Any]:
    v = snapshot.values
    if isinstance(v, BaseModel):
        return v.model_dump(mode="json")
    return dict(v or {})


def _actor() -> tuple[str, str]:
    p = _principal()
    return ("human" if is_human_principal() else "system"), p.user_id


def _emit(action: str, application_id: str, **metadata: Any) -> None:
    actor_type, actor_id = _actor()
    events.emit_event(action, entity_type="application", entity_id=application_id,
                      actor_type=actor_type, actor_id=actor_id, metadata=metadata or None)


# ── public API ───────────────────────────────────────────────────────────
def start_thread(application_id: str) -> dict[str, Any]:
    """Create the application's thread and run it to its first gate."""
    p = _principal()
    app = repository.get_application(application_id)
    if app is None:
        raise NotOrchestrated(f"application {application_id} not found for this tenant")
    if not app.get("orchestrated"):
        raise NotOrchestrated(f"application {application_id} is not orchestrated (rollout flag off)")
    g = graph_mod.get_graph()
    config = _config(application_id, p)
    if _values(g.get_state(config)):
        raise OrchestratorError(f"application {application_id} already has a thread")

    initial = ThreadState(
        application_id=app["id"], candidate_id=app["candidate_id"], job_id=app["job_id"],
        tenant_id=p.tenant_id, graph_version=graph_mod.GRAPH_VERSION, started_by=p.user_id,
        status=ThreadStatus.running, orchestrated=True,
    )
    repository.update_application(application_id, {"graph_version": graph_mod.GRAPH_VERSION})
    _emit("orchestrator.thread_started", application_id, graph_version=graph_mod.GRAPH_VERSION)
    g.invoke(initial, config)
    return thread_status(application_id)


def resume_thread(application_id: str, resume: dict[str, Any]) -> dict[str, Any]:
    """Deliver a human decision or an external event to the waiting node."""
    p = _principal()
    g = graph_mod.get_graph()
    config = _config(application_id, p)
    snapshot = g.get_state(config)
    values = _values(snapshot)
    if not values:
        raise OrchestratorError(f"application {application_id} has no thread")
    if values.get("tenant_id") != p.tenant_id:
        raise TenantMismatch("thread belongs to another tenant")
    if values.get("status") == ThreadStatus.stranded.value:
        raise ThreadStranded(f"application {application_id} is stranded; use the legacy endpoints")

    from_version = int(values.get("graph_version") or 0)
    if from_version < graph_mod.GRAPH_VERSION:
        try:
            migrated = migrations.migrate(dict(values), graph_mod.GRAPH_VERSION)
        except migrations.StrandedThread as exc:
            g.update_state(config, {"status": ThreadStatus.stranded})
            repository.update_application(application_id, {"human_review_requested": True})
            _emit("orchestrator.thread_stranded", application_id,
                  from_version=exc.from_version, to_version=exc.to_version)
            raise ThreadStranded(str(exc)) from exc
        g.update_state(config, migrated)
        _emit("orchestrator.thread_migrated", application_id,
              from_version=from_version, to_version=graph_mod.GRAPH_VERSION)

    waiting_on = _waiting_node(snapshot)
    try:
        g.invoke(Command(resume=resume), config)
    except Exception as exc:  # noqa: BLE001 — discard the planted resume, record, re-raise
        # LangGraph persists a Command(resume=...) value as a pending write on
        # the checkpoint BEFORE the node runs. If the node then fails, that
        # value would survive and be consumed by the NEXT resume — e.g. a
        # system-planted "reject" executed under the next human's authority.
        # Writing a fresh checkpoint (no values) drops every pending write.
        g.update_state(config, None)
        refused = exc if isinstance(exc, HumanActorRequired) else exc.__cause__
        if isinstance(refused, HumanActorRequired):
            _emit("orchestrator.reject_refused", application_id, waiting_on=waiting_on,
                  resume={k: v for k, v in resume.items() if k in ("decision", "event", "event_id")})
            raise refused from None
        raise
    return thread_status(application_id)


def _waiting_node(snapshot) -> str | None:
    """The node the thread is parked on: the next node to run, or — right after
    a failed resume — the task that still holds an error or a pending interrupt."""
    if snapshot.next:
        return snapshot.next[0]
    for task in snapshot.tasks or ():
        if getattr(task, "error", None) is not None or getattr(task, "interrupts", ()):
            return task.name
    return None


def thread_status(application_id: str) -> dict[str, Any] | None:
    """Where the thread is: status, the node it waits on, version, decisions."""
    p = _principal()
    g = graph_mod.get_graph()
    snapshot = g.get_state(_config(application_id, p))
    values = _values(snapshot)
    if not values:
        return None
    if values.get("tenant_id") != p.tenant_id:
        raise TenantMismatch("thread belongs to another tenant")

    nxt = _waiting_node(snapshot)
    status = values.get("status")
    if isinstance(status, ThreadStatus):
        status = status.value
    if status != ThreadStatus.stranded.value and nxt in _WAITING:
        status = _WAITING[nxt]
    decisions = [d.model_dump(mode="json") if isinstance(d, BaseModel) else d
                 for d in (values.get("decisions") or [])]
    return {
        "application_id": application_id,
        "status": status,
        "waiting_on": nxt if nxt in _WAITING else None,
        "graph_version": values.get("graph_version"),
        "started_by": values.get("started_by"),
        "last_event_id": values.get("last_event_id"),
        "decisions": decisions,
    }
