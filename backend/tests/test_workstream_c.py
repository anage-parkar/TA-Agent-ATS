"""Workstream C — human-in-the-loop rejection.

Proves: no solely-automated rejection (unattended/AI context is blocked at the
mutation layer), every reject is audited with a human actor, AI scoring can rank
but never reject, bulk reject records a human decision, and candidates can
request human review.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from db import repository
from db.repository import HumanActorRequired
from services.tenant_context import Principal, use_principal

T = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

_ALL_STORES = (
    "_jobs", "_candidates", "_applications", "_emails", "_generated_jds", "_orgs",
    "_activity_events", "_ai_decisions", "_scorecards", "_consents", "_eeo",
    "_resumes", "_pipeline_stages",
)


@pytest.fixture(autouse=True)
def _force_inmemory_and_clear(monkeypatch):
    monkeypatch.setattr(repository, "db_available", lambda: False)
    for name in _ALL_STORES:
        getattr(repository, name).clear()
    yield
    for name in _ALL_STORES:
        getattr(repository, name).clear()


def _seed_app(tenant_role: Principal):
    with use_principal(tenant_role):
        job = repository.create_job({"title": "Eng"})
        cand = repository.upsert_candidate({"full_name": "Ada", "email": "ada@x.com"})
        app = repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})
        repository.update_application(app["id"], {"status": "scored", "ats_score": 40.0})
        return job, cand, app


def test_unattended_context_cannot_reject():
    """No bound principal (cron/agent) → rejection is refused at the mutation layer."""
    # Seed under a human, then attempt to reject from an UNBOUND context.
    _job, _cand, app = _seed_app(Principal(T, "u-recruiter", "recruiter"))
    with pytest.raises(HumanActorRequired):
        repository.update_application(app["id"], {"status": "rejected", "recruiter_decision": "reject"})


def test_ai_can_score_but_not_reject():
    """Same unattended context may set 'scored' but never 'rejected'."""
    # Unbound = simulates the automated scoring/agent context (default tenant).
    job = repository.create_job({"title": "Eng"})
    cand = repository.upsert_candidate({"full_name": "Bo", "email": "bo@x.com"})
    app = repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})
    # scoring-style update is allowed
    assert repository.update_application(app["id"], {"status": "scored", "ats_score": 30.0}) is not None
    # but rejecting from this unattended context is blocked
    with pytest.raises(HumanActorRequired):
        repository.update_application(app["id"], {"status": "rejected"})


def test_human_reject_is_audited_with_human_actor():
    job, cand, app = _seed_app(Principal(T, "u-recruiter", "recruiter"))
    with use_principal(Principal(T, "u-recruiter", "recruiter")):
        repository.update_application(app["id"], {"status": "rejected", "recruiter_decision": "reject"})
        events = repository.list_activity_events("application", app["id"])

    rejects = [e for e in events if e["action"] == "application.updated" and e["after"].get("status") == "rejected"]
    assert len(rejects) == 1
    ev = rejects[0]
    assert ev["actor_type"] == "human"
    assert ev["actor_id"] == "u-recruiter"
    assert ev["before"]["status"] == "scored"


def test_bulk_reject_records_human_per_app():
    # Seed two scored, undecided apps in the default tenant (TestClient uses it).
    job = repository.create_job({"title": "Eng"})
    ids = []
    for em in ("a@x.com", "b@x.com"):
        c = repository.upsert_candidate({"full_name": em, "email": em})
        a = repository.create_application({"job_id": job["id"], "candidate_id": c["id"]})
        repository.update_application(a["id"], {"status": "scored", "ats_score": 35.0})
        ids.append(a["id"])

    import main

    client = TestClient(main.app)
    resp = client.post(
        f"/api/jobs/{job['id']}/bulk-reject",
        json={"below_threshold": 60},
        headers={"X-User-Role": "recruiter"},
    )
    assert resp.status_code == 200
    assert resp.json()["rejected"] == 2

    for app_id in ids:
        events = repository.list_activity_events("application", app_id)
        rejects = [e for e in events if e["after"].get("status") == "rejected"]
        assert rejects and rejects[0]["actor_type"] == "human"


def test_review_queue_flags_sub_threshold():
    job = repository.create_job({"title": "Eng"})
    c = repository.upsert_candidate({"full_name": "Lo", "email": "lo@x.com"})
    a = repository.create_application({"job_id": job["id"], "candidate_id": c["id"]})
    repository.update_application(a["id"], {"status": "scored", "ats_score": 42.0})

    import main

    client = TestClient(main.app)
    resp = client.get(f"/api/jobs/{job['id']}/review-queue?threshold=60")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["below_threshold"] == 1
    assert data["queue"][0]["below_threshold"] is True


def test_candidate_can_request_human_review():
    job = repository.create_job({"title": "Eng"})
    c = repository.upsert_candidate({"full_name": "Mo", "email": "mo@x.com"})
    a = repository.create_application({"job_id": job["id"], "candidate_id": c["id"]})

    import main

    client = TestClient(main.app)
    resp = client.post(f"/api/applications/{a['id']}/request-human-review")  # public, no role
    assert resp.status_code == 200
    assert resp.json()["human_review_requested"] is True

    events = repository.list_activity_events("application", a["id"])
    req = [e for e in events if e["action"] == "candidate.requested_human_review"]
    assert req and req[0]["actor_type"] == "candidate"
