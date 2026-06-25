"""Workstream B: candidate-as-a-person identity resolution, append-only audit
events, explainable ai_decisions, and EEO segregation from scoring."""

from __future__ import annotations

import json

import pytest

from agents.scoring import score_candidate_full
from db import repository
from models.candidate import CandidateProfile
from models.job import ParsedJob
from services.tenant_context import use_tenant

from .conftest import RecordingProvider

T = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

_RUBRIC_JSON = json.dumps({
    "skill_match": {"score": 0.8, "evidence": "Python"},
    "experience_fit": {"score": 0.7, "evidence": "backend"},
    "tech_stack_overlap": {"score": 0.6, "evidence": "stack"},
})

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


def test_one_person_many_applications():
    """One human applying to three roles → one candidate, three applications."""
    with use_tenant(T):
        for title in ("Backend Engineer", "Platform Engineer", "Staff Engineer"):
            job = repository.create_job({"title": title})
            cand = repository.upsert_candidate({"full_name": "Ada Lovelace", "email": "ada@x.com"})
            repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})

        export = repository.export_tenant(T)
    assert len(export["candidates"]) == 1
    assert len(export["applications"]) == 3


def test_identity_resolution_by_phone_without_email():
    with use_tenant(T):
        c1 = repository.upsert_candidate({"full_name": "Sam", "phone": "(555) 123-4567"})
        c2 = repository.upsert_candidate({"full_name": "Samuel", "phone": "555-123-4567"})
    assert c1["id"] == c2["id"]  # same person by phone hash


def test_every_state_change_writes_audit_event():
    with use_tenant(T):
        job = repository.create_job({"title": "Eng"})
        cand = repository.upsert_candidate({"full_name": "Ada", "email": "ada@x.com"})
        app = repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})

        created = repository.list_activity_events("application", app["id"])
        assert [e["action"] for e in created] == ["application.created"]

        repository.update_application(app["id"], {"status": "approved", "stage": "Reviewed"})
        events = repository.list_activity_events("application", app["id"])
        actions = [e["action"] for e in events]
        assert "application.created" in actions and "application.updated" in actions

        upd = [e for e in events if e["action"] == "application.updated"][0]
        assert upd["before"]["status"] == "sourced"
        assert upd["after"]["status"] == "approved"
        assert upd["actor_id"] is not None  # actor recorded on the change


def test_ai_decision_recorded_with_versions_and_evidence(make_gateway):
    make_gateway(RecordingProvider(text=_RUBRIC_JSON, model_version="claude-sonnet-4-6"))
    with use_tenant(T):
        job = ParsedJob(title="Eng", skills_required=["Python"])
        cand = CandidateProfile(full_name="Ada", skills=["Python"])
        res = score_candidate_full(job, cand)
        meta, breakdown = res.llm_result, res.breakdown
        assert meta.prompt_name == "scoring.rubric"

        j = repository.create_job({"title": "Eng"})
        c = repository.upsert_candidate({"full_name": "Ada", "email": "ada@x.com"})
        a = repository.create_application({"job_id": j["id"], "candidate_id": c["id"]})
        repository.create_ai_decision({
            "application_id": a["id"], "candidate_id": c["id"], "job_id": j["id"],
            "kind": "ats_score", "model_version": meta.model_version,
            "prompt_name": meta.prompt_name, "prompt_version": meta.prompt_version,
            "prompt_hash": meta.prompt_hash, "input_hash": meta.input_hash,
            "scores": breakdown.model_dump(), "evidence": res.evidence,
        })

        decisions = repository.list_ai_decisions_for_application(a["id"])
    assert len(decisions) == 1
    d = decisions[0]
    assert d["kind"] == "ats_score"
    assert d["model_version"] == "claude-sonnet-4-6"
    assert d["prompt_version"] == 2   # scoring.rubric bumped to v2 in Workstream E
    assert d["input_hash"]  # non-empty
    assert d["scores"]["overall_score"] == 69.5   # computed from default weights
    assert d["evidence"]["skill_match"] == "Python"


def test_eeo_data_never_enters_scoring_prompt(make_gateway):
    provider = RecordingProvider(text=_RUBRIC_JSON)
    make_gateway(provider)
    with use_tenant(T):
        c = repository.upsert_candidate({"full_name": "Ada", "email": "ada@x.com"})
        repository.create_eeo_record(
            {"candidate_id": c["id"], "data": {"ethnicity": "SECRET_EEO_MARKER", "gender": "x"}}
        )
        score_candidate_full(ParsedJob(title="Eng"), CandidateProfile(full_name="Ada", skills=["Python"]))

    sent_prompt = provider.calls[-1]["user"]
    assert "SECRET_EEO_MARKER" not in sent_prompt          # scoring never sees EEO data
    assert len(repository._eeo) == 1                       # but it is stored (segregated)
