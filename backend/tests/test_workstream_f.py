"""Workstream F — compliance & bias infrastructure.

Proves: per-tenant adverse-impact report; per-candidate export + erasure
(tenant-scoped, audited); consent + AI-disclosure captured and surfaced;
automated retention deletes expired candidates; published sub-processor list.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from db import repository
from services import bias, retention
from services.tenant_context import use_tenant

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


def test_adverse_impact_flags_four_fifths_violation():
    with use_tenant(T):
        job = repository.create_job({"title": "Eng"})
        # Group A: selected; Group B: not selected → 0/ rate, four-fifths violated.
        plan = [("A", True), ("A", True), ("B", False), ("B", False)]
        for i, (grp, selected) in enumerate(plan):
            c = repository.upsert_candidate({"full_name": f"Cand {i}", "email": f"c{i}@x.com"})
            a = repository.create_application({"job_id": job["id"], "candidate_id": c["id"]})
            repository.create_eeo_record({"candidate_id": c["id"], "data": {"gender": grp}})
            if selected:
                repository.update_application(a["id"], {"status": "approved", "recruiter_decision": "proceed"})

        report = bias.adverse_impact(min_group=1)

    gender = report["attributes"]["gender"]
    assert gender["four_fifths_violation"] is True
    rates = {g["group"]: g["selection_rate"] for g in gender["groups"]}
    assert rates["A"] == 1.0 and rates["B"] == 0.0


def test_candidate_export_collects_all_data():
    with use_tenant(T):
        c = repository.upsert_candidate({"full_name": "Ada", "email": "ada@x.com"})
        job = repository.create_job({"title": "Eng"})
        repository.create_application({"job_id": job["id"], "candidate_id": c["id"]})
        repository.create_consent({"candidate_id": c["id"], "lawful_basis": "consent"})
        repository.create_eeo_record({"candidate_id": c["id"], "data": {"gender": "A"}})

        export = repository.export_candidate(c["id"])
    assert export["candidate"]["id"] == c["id"]
    assert len(export["applications"]) == 1
    assert len(export["consent_records"]) == 1
    assert len(export["eeo_records"]) == 1


def test_candidate_erasure_is_scoped_and_audited():
    with use_tenant(T):
        c1 = repository.upsert_candidate({"full_name": "A", "email": "a@x.com"})
        c2 = repository.upsert_candidate({"full_name": "B", "email": "b@x.com"})
        job = repository.create_job({"title": "Eng"})
        repository.create_application({"job_id": job["id"], "candidate_id": c1["id"]})
        repository.create_consent({"candidate_id": c1["id"], "lawful_basis": "consent"})
        repository.create_eeo_record({"candidate_id": c1["id"], "data": {"gender": "A"}})

        repository.erase_candidate(c1["id"])

        assert repository.get_candidate(c1["id"]) is None
        assert repository.export_candidate(c1["id"]) is None
        assert repository.get_candidate(c2["id"]) is not None      # other candidate untouched
        assert repository._vals(repository._consents) == []
        assert repository._vals(repository._eeo) == []
        events = repository.list_activity_events("candidate", c1["id"])
        assert any(e["action"] == "candidate.erased" for e in events)


def test_retention_auto_deletes_expired_candidates():
    with use_tenant(T):
        repository.update_organization(T, {"retention_days": 30})
        old = repository.upsert_candidate({"full_name": "Old", "email": "old@x.com"})
        repository._candidates[old["id"]]["created_at"] = "2000-01-01T00:00:00+00:00"
        fresh = repository.upsert_candidate({"full_name": "New", "email": "new@x.com"})

        result = retention.enforce(T)

        assert result["status"] == "ok"
        assert result["candidates_erased"] == 1
        assert repository.get_candidate(old["id"]) is None
        assert repository.get_candidate(fresh["id"]) is not None


def test_retention_disabled_when_unset():
    with use_tenant(T):
        assert retention.enforce(T)["status"] == "disabled"


# ── public candidate transparency / consent + sub-processors ──────────
def test_consent_and_transparency_surface_to_candidate():
    # Seed in the default tenant (TestClient requests use it).
    job = repository.create_job({"title": "Eng"})
    c = repository.upsert_candidate({"full_name": "Ada", "email": "ada@x.com"})
    app = repository.create_application({"job_id": job["id"], "candidate_id": c["id"]})

    import main

    client = TestClient(main.app)

    r = client.post(
        f"/api/applications/{app['id']}/consent",
        json={"lawful_basis": "consent", "scope": "hiring", "region": "EU"},
    )
    assert r.status_code == 200

    r = client.get(f"/api/applications/{app['id']}/transparency")
    assert r.status_code == 200
    data = r.json()
    assert "AI-assisted" in data["ai_disclosure"]
    assert data["request_human_review_url"].endswith("/request-human-review")
    assert len(data["consent_on_file"]) == 1
    assert data["consent_on_file"][0]["region"] == "EU"


def test_subprocessors_published():
    import main

    client = TestClient(main.app)
    r = client.get("/api/legal/subprocessors")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["subprocessors"]}
    assert "Anthropic" in names
