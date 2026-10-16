"""Phase 2 / PR 2 — graph skeleton: principal propagation, interrupt gates,
and the three tests the design note names (T1–T3), written before the graph.

Everything runs on the in-memory repository and the in-memory checkpointer
with a fake scorer (no LLM). The rollout flag is off everywhere: tests flip
`orchestrated` on individual applications directly in the store.
"""

from __future__ import annotations

import pytest

from db import repository
from db.repository import HumanActorRequired
from orchestrator import checkpointer as cp
from orchestrator import graph as graph_mod
from orchestrator import runner
from orchestrator.state import ScoreSummary
from services.config import settings
from services.tenant_context import use_system, use_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
RECRUITER_1 = "11111111-1111-4111-8111-111111111111"
RECRUITER_2 = "22222222-2222-4222-8222-222222222222"

_ALL_STORES = (
    "_jobs", "_candidates", "_applications", "_emails", "_generated_jds", "_orgs",
    "_activity_events", "_ai_decisions", "_scorecards", "_consents", "_eeo",
    "_resumes", "_pipeline_stages", "_interviews",
)


@pytest.fixture(autouse=True)
def _memory_everything(monkeypatch):
    monkeypatch.setattr(repository, "db_available", lambda: False)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "checkpointer_backend", "memory")
    cp.reset_checkpointer()
    graph_mod.reset_graph()
    # No LLM in tests: scoring returns a fixed, above-threshold summary.
    monkeypatch.setattr(graph_mod, "score_application",
                        lambda application: ScoreSummary(overall=72.0, passed_knockouts=True, below_threshold=False))
    for name in _ALL_STORES:
        getattr(repository, name).clear()
    yield
    cp.reset_checkpointer()
    graph_mod.reset_graph()
    for name in _ALL_STORES:
        getattr(repository, name).clear()


def _seed(tenant=A, orchestrated=True) -> dict:
    """Job + candidate + application under `tenant`; flips the frozen flag."""
    with use_tenant(tenant):
        job = repository.create_job({"title": "Eng"})
        cand = repository.upsert_candidate({"full_name": "Ada", "email": f"ada-{tenant[:2]}@x.com"})
        app = repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})
        repository._applications[app["id"]]["orchestrated"] = orchestrated
    return app


def _events(action: str, entity_id: str | None = None) -> list[dict]:
    return [e for e in repository._activity_events.values()
            if e.get("action") == action and (entity_id is None or e.get("entity_id") == entity_id)]


def _app(app_id: str) -> dict:
    return repository._applications[app_id]


# ── T1: the system resumes a human-started thread and cannot reject ───
def test_t1_system_resume_cannot_reject_a_human_started_thread():
    app = _seed()
    with use_tenant(A, user_id=RECRUITER_1):
        status = runner.start_thread(app["id"])
    assert status["status"] == "waiting_human" and status["waiting_on"] == "review_gate"
    before = dict(_app(app["id"]))

    with use_system(A, actor="orchestrator"):
        with pytest.raises(HumanActorRequired):
            runner.resume_thread(app["id"], {"decision": "reject", "event_id": "evt:1"})

    with use_tenant(A, user_id=RECRUITER_1):
        status = runner.thread_status(app["id"])
    assert status["status"] == "waiting_human" and status["waiting_on"] == "review_gate"  # still interrupted
    assert _app(app["id"])["status"] == before["status"]                                  # unchanged
    assert _app(app["id"]).get("recruiter_decision") is None
    refused = _events("orchestrator.reject_refused", app["id"])
    assert refused and refused[0]["actor_type"] == "system" and refused[0]["actor_id"] == "system:orchestrator"

    # The planted "reject" must not survive the refusal: LangGraph persists a
    # resume value before the node runs, so the runner discards it on failure.
    # A human who now proceeds must get PROCEED, not the system's reject.
    with use_tenant(A, user_id=RECRUITER_2):
        status = runner.resume_thread(app["id"], {"decision": "proceed", "event_id": "evt:1b"})
    assert status["waiting_on"] == "send_gate"
    assert _app(app["id"])["status"] == "approved"
    assert status["decisions"][-1]["outcome"] == "proceed" and status["decisions"][-1]["by"] == RECRUITER_2


# ── T2: a different human resumes; the resuming human is the actor ────
def test_t2_resuming_human_not_the_starter_is_the_audited_actor():
    app = _seed()
    with use_tenant(A, user_id=RECRUITER_1):
        runner.start_thread(app["id"])

    with use_tenant(A, user_id=RECRUITER_2):
        status = runner.resume_thread(app["id"], {"decision": "reject", "event_id": "evt:2"})

    assert status["status"] == "closed"
    assert _app(app["id"])["status"] == "rejected"
    updated = [e for e in _events("application.updated", app["id"]) if (e.get("after") or {}).get("status") == "rejected"]
    assert updated and updated[-1]["actor_type"] == "human"
    assert updated[-1]["actor_id"] == RECRUITER_2            # the resuming human…
    assert updated[-1]["actor_id"] != RECRUITER_1            # …never the thread starter
    assert status["decisions"][-1]["by"] == RECRUITER_2
    assert status["started_by"] == RECRUITER_1               # recorded as context only


# ── T3: the system resumes with a non-adverse event ───────────────────
def test_t3_system_resume_with_an_event_succeeds_and_is_audited_to_the_orchestrator():
    app = _seed()
    with use_tenant(A, user_id=RECRUITER_1):
        runner.start_thread(app["id"])
        assert runner.resume_thread(app["id"], {"decision": "proceed", "event_id": "evt:3"})["waiting_on"] == "send_gate"
        assert _app(app["id"])["status"] == "approved" and _app(app["id"])["stage"] == "Reviewed"
        status = runner.resume_thread(app["id"], {"approve": True, "event_id": "evt:4"})
    assert status["status"] == "waiting_event" and status["waiting_on"] == "await_reply"
    assert repository._emails == {}                           # PR 2 records approval only; nothing is sent

    with use_system(A, actor="orchestrator"):
        status = runner.resume_thread(app["id"], {"event": "reply.received", "event_id": "evt:5"})

    assert status["status"] == "waiting_human" and status["waiting_on"] == "interview_gate"
    received = _events("orchestrator.event_received", app["id"])
    assert received and received[0]["actor_type"] == "system" and received[0]["actor_id"] == "system:orchestrator"
    assert status["last_event_id"] == "evt:5"


# ── principal discipline ──────────────────────────────────────────────
def test_unbound_principal_is_refused_not_defaulted():
    app = _seed()
    with pytest.raises(runner.NoPrincipalBound):
        runner.start_thread(app["id"])
    with use_tenant(A):
        runner.start_thread(app["id"])
    with pytest.raises(runner.NoPrincipalBound):
        runner.resume_thread(app["id"], {"decision": "proceed", "event_id": "evt:6"})


def test_another_tenant_cannot_resume_or_see_the_thread():
    """Braces: the checkpointer itself (RLS in Postgres, ownership in memory)
    returns nothing for another tenant, so the runner sees no thread at all."""
    app = _seed(A)
    with use_tenant(A):
        runner.start_thread(app["id"])
    with use_tenant(B):
        with pytest.raises(runner.OrchestratorError):
            runner.resume_thread(app["id"], {"decision": "reject", "event_id": "evt:7"})
        assert runner.thread_status(app["id"]) is None
    with use_tenant(A):
        assert runner.thread_status(app["id"])["waiting_on"] == "review_gate"
    assert _app(app["id"])["status"] != "rejected"


def test_tenant_mismatch_belt_refuses_a_leaked_snapshot(monkeypatch):
    """Belt: even if the checkpoint layer handed another tenant's state to the
    runner, the runner compares the state's tenant with the bound principal."""
    app = _seed(A)
    with use_tenant(A):
        runner.start_thread(app["id"])
        g = graph_mod.get_graph()
        leaked = g.get_state(runner._config(app["id"], runner._principal()))
    monkeypatch.setattr(g, "get_state", lambda config: leaked)
    with use_tenant(B):
        with pytest.raises(runner.TenantMismatch):
            runner.resume_thread(app["id"], {"decision": "reject", "event_id": "evt:7"})
        with pytest.raises(runner.TenantMismatch):
            runner.thread_status(app["id"])
    assert _app(app["id"])["status"] != "rejected"


def test_non_orchestrated_application_has_no_thread():
    app = _seed(orchestrated=False)
    with use_tenant(A):
        with pytest.raises(runner.NotOrchestrated):
            runner.start_thread(app["id"])


def test_rollout_flag_is_off_everywhere_by_default():
    with use_tenant(A):
        job = repository.create_job({"title": "Eng"})
        cand = repository.upsert_candidate({"full_name": "Ada", "email": "ada@x.com"})
        app = repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})
    assert job.get("orchestrator_mode", "off") == "off"
    assert app.get("orchestrated", False) is False


# ── gate nodes have no side effects before the interrupt ──────────────
def test_starting_a_thread_mutates_nothing_until_a_human_decides():
    app = _seed()
    before = {k: v for k, v in _app(app["id"]).items() if k != "updated_at"}
    n_events = len(repository._activity_events)
    with use_tenant(A, user_id=RECRUITER_1):
        runner.start_thread(app["id"])
    after = {k: v for k, v in _app(app["id"]).items() if k != "updated_at"}
    assert {k: v for k, v in after.items() if k != "graph_version"} == {k: v for k, v in before.items() if k != "graph_version"}
    assert after["graph_version"] == graph_mod.GRAPH_VERSION    # the only write: the version stamp
    new_events = list(repository._activity_events.values())[n_events:]
    assert [e["action"] for e in new_events] == ["orchestrator.thread_started"]


# ── versioning scaffold ───────────────────────────────────────────────
def test_every_graph_version_has_a_migration_path_or_an_explicit_no_migration():
    from orchestrator import migrations

    for v in range(1, graph_mod.GRAPH_VERSION):
        assert v in migrations.MIGRATIONS, f"no migration or NO_MIGRATION entry for graph version {v}"


def test_thread_from_an_older_graph_without_a_path_is_stranded_into_human_review(monkeypatch):
    from orchestrator import migrations

    app = _seed()
    with use_tenant(A, user_id=RECRUITER_1):
        runner.start_thread(app["id"])
    # A deploy bumps the graph version with no migration from v1.
    monkeypatch.setattr(graph_mod, "GRAPH_VERSION", graph_mod.GRAPH_VERSION + 1)
    monkeypatch.setitem(migrations.MIGRATIONS, graph_mod.GRAPH_VERSION - 1, migrations.NO_MIGRATION)

    with use_tenant(A, user_id=RECRUITER_2):
        with pytest.raises(runner.ThreadStranded):
            runner.resume_thread(app["id"], {"decision": "proceed", "event_id": "evt:8"})
        assert runner.thread_status(app["id"])["status"] == "stranded"
    assert _app(app["id"])["human_review_requested"] is True
    stranded = _events("orchestrator.thread_stranded", app["id"])
    assert stranded and stranded[0]["metadata"]["from_version"] == 1
    assert _app(app["id"])["status"] != "approved"            # the decision was NOT applied


def test_thread_from_an_older_graph_with_a_path_is_migrated_on_resume(monkeypatch):
    from orchestrator import migrations

    app = _seed()
    with use_tenant(A, user_id=RECRUITER_1):
        runner.start_thread(app["id"])
    monkeypatch.setattr(graph_mod, "GRAPH_VERSION", graph_mod.GRAPH_VERSION + 1)
    monkeypatch.setitem(migrations.MIGRATIONS, graph_mod.GRAPH_VERSION - 1,
                        lambda values: {**values, "graph_version": values["graph_version"] + 1})

    with use_tenant(A, user_id=RECRUITER_2):
        status = runner.resume_thread(app["id"], {"decision": "proceed", "event_id": "evt:9"})
    assert status["graph_version"] == graph_mod.GRAPH_VERSION
    assert status["waiting_on"] == "send_gate"               # the decision applied after migrating
    migrated = _events("orchestrator.thread_migrated", app["id"])
    assert migrated and migrated[0]["metadata"] == {"from_version": 1, "to_version": 2}


# ── the decision endpoint routes orchestrated applications to the graph ─
def test_decision_endpoint_resumes_the_thread_for_orchestrated_applications():
    from fastapi.testclient import TestClient
    import main

    app = _seed()
    with use_tenant(A, user_id=RECRUITER_1):
        runner.start_thread(app["id"])
    client = TestClient(main.app)
    r = client.post(f"/api/candidates/{app['id']}/decision", json={"decision": "proceed"},
                    headers={"X-Tenant-Id": A, "X-User-Id": RECRUITER_2, "X-User-Role": "recruiter"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved" and r.json()["thread"]["waiting_on"] == "send_gate"
    assert _app(app["id"])["status"] == "approved"


def test_decision_endpoint_keeps_the_legacy_path_for_non_orchestrated_applications():
    from fastapi.testclient import TestClient
    import main

    app = _seed(orchestrated=False)
    r = TestClient(main.app).post(f"/api/candidates/{app['id']}/decision", json={"decision": "proceed"},
                                  headers={"X-Tenant-Id": A, "X-User-Id": RECRUITER_1, "X-User-Role": "recruiter"})
    assert r.status_code == 200 and "thread" not in r.json()
    assert _app(app["id"])["status"] == "approved" and _app(app["id"])["stage"] == "Reviewed"
    with use_tenant(A):
        assert cp.get_checkpointer().get_tuple({"configurable": {"thread_id": app["id"], "checkpoint_ns": ""}}) is None
