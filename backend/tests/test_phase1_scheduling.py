"""Phase 1 — scheduled automation (Celery beat): reply polling + retention.

This is the first code that loops across tenants with no human present, so the
proofs here are about running it unattended safely:

* each tenant is bound in turn under the least-privilege *system* principal
  and unbound afterwards; one tenant's failure does not stop the rest;
* the beat schedule is driven by config (every 5 min / daily 03:00 UTC);
* the shared reply poller never records org A's replies under org B, never
  records the same message twice, and the on-demand route and the scheduled
  path produce the same result;
* retention erases only expired candidates of tenants that opted in, and
  every erasure is audited to the system actor;
* the human-actor gate on rejection still holds for the scheduler, and the
  system role cannot be obtained over HTTP.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from celery.schedules import crontab
from fastapi.testclient import TestClient

from db import repository
from services import email_service, reply_poller, retention, tenant_context
from services.config import settings
from services.rbac import PERMISSIONS, has_permission
from services.tenant_context import (
    SYSTEM_ROLE,
    get_principal,
    is_human_principal,
    is_system_principal,
    use_system,
    use_tenant,
)
from tasks import jobs
from tasks.celery_app import celery_app

_ALL_STORES = (
    "_jobs", "_candidates", "_applications", "_emails", "_generated_jds", "_orgs",
    "_activity_events", "_ai_decisions", "_scorecards", "_consents", "_eeo",
    "_resumes", "_pipeline_stages", "_interviews",
)

_OLD = "2000-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _force_inmemory_and_clear(monkeypatch):
    monkeypatch.setattr(repository, "db_available", lambda: False)
    # Never touch the real Gmail API; individual tests swap in a fake mailbox.
    monkeypatch.setattr(email_service, "availability", lambda: (False, "disabled in tests"))
    for name in _ALL_STORES:
        getattr(repository, name).clear()
    yield
    for name in _ALL_STORES:
        getattr(repository, name).clear()


# ── helpers ───────────────────────────────────────────────────────────
def _seed_outbound(thread_id: str, email: str = "cand@x.com") -> dict:
    """Job + candidate + application + one outbound email in `thread_id`,
    all under whichever tenant is currently bound."""
    job = repository.create_job({"title": "Eng"})
    cand = repository.upsert_candidate({"full_name": "Ada", "email": email})
    app = repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})
    repository.create_email({
        "application_id": app["id"], "direction": "outbound",
        "subject": "Hi", "body": "Hello", "thread_id": thread_id,
    })
    return app


class FakeMailbox:
    """Stands in for Gmail. Deliberately returns *every* reply it holds no
    matter which thread ids it was asked for (a shared mailbox), so the tests
    prove the poller itself only keeps replies for the bound tenant's threads.
    Records the thread-id set of each poll."""

    def __init__(self, replies: list[dict]):
        self.replies = replies
        self.asked: list[set[str]] = []

    def list_replies(self, thread_ids, days: int = 14):
        self.asked.append(set(thread_ids))
        return list(self.replies)


def _reply(thread_id: str, body: str = "I'm interested!") -> dict:
    return {"thread_id": thread_id, "from": "cand@x.com", "body": body, "message_id": f"m-{thread_id}"}


def _mock_gmail(monkeypatch, mailbox: FakeMailbox, intent: str = "interested"):
    monkeypatch.setattr(email_service, "availability", lambda: (True, "ok"))
    monkeypatch.setattr(email_service, "list_replies", mailbox.list_replies)
    # No LLM in tests: every reply classifies as `intent`.
    monkeypatch.setattr(reply_poller, "classify_reply", lambda body: SimpleNamespace(intent=intent))


def _three_orgs(monkeypatch) -> list[str]:
    orgs = [{"id": f"org-{k}"} for k in ("a", "b", "c")]
    monkeypatch.setattr(repository, "list_organizations", lambda: orgs)
    return [o["id"] for o in orgs]


def _client() -> TestClient:
    import main
    return TestClient(main.app)


# ── the system principal ──────────────────────────────────────────────
def test_system_principal_scopes_tenant_but_is_not_human_and_has_no_permissions():
    with use_system("t-1", actor="scheduler") as p:
        assert p.role == SYSTEM_ROLE and p.user_id == "system:scheduler"
        assert tenant_context.get_tenant_id() == "t-1"
        assert is_system_principal() and not is_human_principal()
        assert not any(has_permission(SYSTEM_ROLE, perm) for perm in PERMISSIONS)
        assert not has_permission(SYSTEM_ROLE, "anything.read")  # not even undeclared reads
    assert tenant_context._principal.get() is None  # unbound on exit


def test_operator_binding_still_counts_as_human():
    with use_tenant("t-1"):
        assert is_human_principal() and not is_system_principal()


def test_rejection_gate_holds_for_the_scheduler_but_stage_advance_is_allowed():
    with use_tenant("t-1"):
        app = _seed_outbound("thread-1")
    with use_system("t-1"):
        with pytest.raises(repository.HumanActorRequired):
            repository.update_application(app["id"], {"status": "rejected"})
        repository.update_application(app["id"], {"stage": "Replied"})  # non-adverse: fine
    with use_tenant("t-1"):
        ev = [e for e in repository.list_activity_events(entity_id=app["id"])
              if e["action"] == "application.updated"]
    assert ev and ev[-1]["actor_type"] == "system" and ev[-1]["actor_id"] == "system:scheduler"
    assert repository._applications[app["id"]]["status"] != "rejected"


def test_system_role_cannot_be_claimed_over_http():
    # auth downgrades unknown roles to least privilege; 'system' is not an HTTP role.
    r = _client().post("/api/emails/poll-replies", headers={"X-User-Role": "system"})
    assert r.status_code == 403


# ── beat schedule ─────────────────────────────────────────────────────
def test_beat_schedule_comes_from_config():
    sched = celery_app.conf.beat_schedule
    poll, ret = sched["poll-email-replies"], sched["enforce-retention"]
    assert poll["task"] == "tasks.jobs.poll_replies_all_tenants"
    assert poll["schedule"] == settings.celery_poll_replies_interval_seconds == 300
    assert ret["task"] == "tasks.jobs.enforce_retention_all_tenants"
    assert isinstance(ret["schedule"], crontab)
    assert ret["schedule"].hour == {settings.celery_retention_hour_utc} == {3}
    assert ret["schedule"].minute == {0}
    assert celery_app.conf.timezone == "UTC"
    for name in (poll["task"], ret["task"]):
        assert name in celery_app.tasks


# ── cross-tenant loop ─────────────────────────────────────────────────
def test_poll_task_binds_each_tenant_as_system_then_unbinds(monkeypatch):
    ids = _three_orgs(monkeypatch)
    seen: list[tuple[str, str, bool]] = []

    def fake_poll():
        p = get_principal()
        seen.append((p.tenant_id, p.role, is_human_principal()))
        return {"ok": True, "replies": 0, "advanced_to_replied": 0}

    monkeypatch.setattr(jobs, "poll_replies", fake_poll)
    result = jobs.poll_replies_all_tenants()

    assert [s[0] for s in seen] == ids
    assert all(role == SYSTEM_ROLE and not human for _, role, human in seen)
    assert set(result) == set(ids)
    assert tenant_context._principal.get() is None  # nothing leaks past the task


def test_poll_task_one_tenant_failure_does_not_stop_others(monkeypatch):
    _three_orgs(monkeypatch)

    def fake_poll():
        if get_principal().tenant_id == "org-b":
            raise RuntimeError("mailbox down")
        return {"ok": True, "replies": 1, "advanced_to_replied": 0}

    monkeypatch.setattr(jobs, "poll_replies", fake_poll)
    result = jobs.poll_replies_all_tenants()

    assert result["org-a"]["ok"] and result["org-c"]["ok"]
    assert result["org-b"]["ok"] is False
    assert tenant_context._principal.get() is None


def test_retention_task_binds_each_tenant_and_survives_one_failure(monkeypatch):
    ids = _three_orgs(monkeypatch)
    seen: list[tuple[str, str]] = []

    def fake_enforce(tenant_id):
        seen.append((tenant_id, get_principal().tenant_id))
        assert is_system_principal()
        if tenant_id == "org-b":
            raise RuntimeError("db hiccup")
        return {"status": "ok", "candidates_erased": 0}

    monkeypatch.setattr(retention, "enforce", fake_enforce)
    result = jobs.enforce_retention_all_tenants()

    assert seen == [(i, i) for i in ids]  # bound tenant always matches the one being enforced
    assert result["org-a"]["status"] == "ok" and result["org-c"]["status"] == "ok"
    assert result["org-b"] == {"status": "error"}
    assert tenant_context._principal.get() is None


def test_poll_task_reports_unconfigured_email_per_tenant_without_raising(monkeypatch):
    ids = _three_orgs(monkeypatch)  # availability is (False, ...) from the fixture
    result = jobs.poll_replies_all_tenants()
    assert set(result) == set(ids)
    assert all(r["ok"] is False and r["detail"] for r in result.values())


# ── reply poller: dedupe + tenant isolation ───────────────────────────
def test_poller_records_each_reply_once_and_only_for_its_own_tenant(monkeypatch):
    org_a = repository.create_organization("A")["id"]
    org_b = repository.create_organization("B")["id"]
    with use_tenant(org_a):
        app_a = _seed_outbound("thread-A", "a@x.com")
    with use_tenant(org_b):
        app_b = _seed_outbound("thread-B", "b@x.com")
    mailbox = FakeMailbox([_reply("thread-A"), _reply("thread-B", "Yes please")])
    _mock_gmail(monkeypatch, mailbox)

    with use_system(org_a):
        first = reply_poller.poll_replies()
        second = reply_poller.poll_replies()

    assert first == {"ok": True, "replies": 1, "advanced_to_replied": 1}
    assert second == {"ok": True, "replies": 0, "advanced_to_replied": 0}  # seen twice → recorded once
    assert mailbox.asked[0] == {"thread-A"}  # A's poll never asks the mailbox for B's threads

    inbound = [e for e in repository._emails.values() if e.get("direction") == "inbound"]
    assert len(inbound) == 1
    assert inbound[0]["tenant_id"] == org_a and inbound[0]["application_id"] == app_a["id"]
    assert repository._applications[app_a["id"]]["stage"] == "Replied"
    assert repository._applications[app_b["id"]]["stage"] != "Replied"  # B untouched by A's poll

    with use_system(org_b):
        assert reply_poller.poll_replies() == {"ok": True, "replies": 1, "advanced_to_replied": 1}
    assert mailbox.asked[-1] == {"thread-B"}
    inbound_b = [e for e in repository._emails.values()
                 if e.get("direction") == "inbound" and e["tenant_id"] == org_b]
    assert len(inbound_b) == 1 and inbound_b[0]["application_id"] == app_b["id"]
    assert repository._applications[app_b["id"]]["stage"] == "Replied"


def test_poller_only_advances_interested_replies(monkeypatch):
    org = repository.create_organization("A")["id"]
    with use_tenant(org):
        app = _seed_outbound("thread-A")
    _mock_gmail(monkeypatch, FakeMailbox([_reply("thread-A", "No thanks")]), intent="not_interested")

    with use_system(org):
        assert reply_poller.poll_replies() == {"ok": True, "replies": 1, "advanced_to_replied": 0}
    assert repository._applications[app["id"]]["stage"] == "Sourced"
    inbound = [e for e in repository._emails.values() if e.get("direction") == "inbound"]
    assert inbound[0]["intent"] == "not_interested"


def test_scheduled_poll_end_to_end_is_audited_to_the_system_actor(monkeypatch):
    org = repository.create_organization("A")["id"]
    with use_tenant(org):
        app = _seed_outbound("thread-A")
    _mock_gmail(monkeypatch, FakeMailbox([_reply("thread-A")]))

    result = jobs.poll_replies_all_tenants()  # real poller, real repository, fake mailbox

    assert result == {org: {"ok": True, "replies": 1, "advanced_to_replied": 1}}
    ev = [e for e in repository._activity_events.values()
          if e.get("entity_id") == app["id"] and e.get("action") == "application.updated"]
    assert ev and ev[-1]["after"]["stage"] == "Replied"
    assert ev[-1]["actor_type"] == "system" and ev[-1]["actor_id"] == "system:scheduler"
    assert ev[-1]["tenant_id"] == org


# ── on-demand route vs scheduled path ─────────────────────────────────
def test_route_and_shared_poller_agree(monkeypatch):
    # The route runs under the request principal (default tenant); seed there.
    app = _seed_outbound("thread-D")
    _mock_gmail(monkeypatch, FakeMailbox([_reply("thread-D")]))

    direct = reply_poller.poll_replies()
    # Reset to the identical pre-poll state, then take the HTTP path.
    for k in [k for k, e in repository._emails.items() if e.get("direction") == "inbound"]:
        del repository._emails[k]
    repository._applications[app["id"]]["stage"] = "Sourced"

    via_route = _client().post("/api/emails/poll-replies")
    assert via_route.status_code == 200
    assert via_route.json() == direct == {"ok": True, "replies": 1, "advanced_to_replied": 1}
    assert repository._applications[app["id"]]["stage"] == "Replied"


def test_route_reports_unconfigured_email_as_ok_false():
    r = _client().post("/api/emails/poll-replies")
    assert r.status_code == 200 and r.json()["ok"] is False


def test_route_surfaces_mailbox_failure_as_502(monkeypatch):
    _seed_outbound("thread-E")
    monkeypatch.setattr(email_service, "availability", lambda: (True, "ok"))

    def boom(thread_ids, days=14):
        raise RuntimeError("gmail 500")

    monkeypatch.setattr(email_service, "list_replies", boom)
    assert _client().post("/api/emails/poll-replies").status_code == 502


# ── retention across tenants ──────────────────────────────────────────
def test_retention_task_erases_only_expired_candidates_of_opted_in_tenants():
    org_a = repository.create_organization("A")["id"]
    org_b = repository.create_organization("B")["id"]
    with use_tenant(org_a):
        repository.update_organization(org_a, {"retention_days": 30})
        old = repository.upsert_candidate({"full_name": "Old", "email": "old@a.com"})
        repository._candidates[old["id"]]["created_at"] = _OLD
        fresh = repository.upsert_candidate({"full_name": "New", "email": "new@a.com"})
    with use_tenant(org_b):  # no retention policy configured
        old_b = repository.upsert_candidate({"full_name": "OldB", "email": "old@b.com"})
        repository._candidates[old_b["id"]]["created_at"] = _OLD

    result = jobs.enforce_retention_all_tenants()

    assert result[org_a]["status"] == "ok" and result[org_a]["candidates_erased"] == 1
    assert result[org_b]["status"] == "disabled"
    assert old["id"] not in repository._candidates      # expired → erased
    assert fresh["id"] in repository._candidates        # newer → kept
    assert old_b["id"] in repository._candidates        # other tenant → untouched

    erased = [e for e in repository._activity_events.values() if e.get("action") == "candidate.erased"]
    assert len(erased) == 1
    assert erased[0]["entity_id"] == old["id"] and erased[0]["tenant_id"] == org_a
    assert erased[0]["actor_type"] == "system" and erased[0]["actor_id"] == "system:scheduler"
    assert tenant_context._principal.get() is None


def test_operator_run_retention_is_audited_as_human():
    org = repository.create_organization("A")["id"]
    with use_tenant(org):
        repository.update_organization(org, {"retention_days": 30})
        old = repository.upsert_candidate({"full_name": "Old", "email": "old@a.com"})
        repository._candidates[old["id"]]["created_at"] = _OLD

    r = _client().post(f"/api/admin/tenants/{org}/retention/run")
    assert r.status_code == 200 and r.json()["candidates_erased"] == 1
    erased = [e for e in repository._activity_events.values() if e.get("action") == "candidate.erased"]
    assert erased and erased[0]["actor_type"] == "human"
