"""Workstream D — redacted, configurable, evidence-backed scoring.

Proves: the LLM only sees a redacted profile (no PII proxies), knockouts are
deterministic and run OUTSIDE the LLM, weights/threshold are per-job
configurable, and re-scoring the same input is stable.
"""

from __future__ import annotations

import json

from agents.scoring import score_candidate_full
from models.candidate import CandidateProfile
from models.job import JobLocation, ParsedJob

from .conftest import RecordingProvider

_RUBRIC = json.dumps({
    "skill_match": {"score": 0.8, "evidence": "Python"},
    "experience_fit": {"score": 0.7, "evidence": "backend"},
    "tech_stack_overlap": {"score": 0.6, "evidence": "stack"},
})


def test_scoring_runs_on_redacted_profile(make_gateway):
    provider = RecordingProvider(text=_RUBRIC)
    make_gateway(provider)
    cand = CandidateProfile(
        full_name="Ada Lovelace", email="ada@example.com",
        headline="She is a brilliant engineer", skills=["Python"],
        location="London",
    )
    score_candidate_full(ParsedJob(title="Eng"), cand)

    user = provider.calls[-1]["user"]
    assert "Ada" not in user and "Lovelace" not in user
    assert "ada@example.com" not in user
    assert "she" not in user.lower()          # gendered pronoun neutralised
    assert "London" not in user               # location handled deterministically, not by LLM


def test_knockouts_are_deterministic_and_skip_the_llm(make_gateway):
    provider = RecordingProvider(text=_RUBRIC)
    make_gateway(provider)
    job = ParsedJob(title="Eng", skills_required=["Python"])
    cand = CandidateProfile(full_name="Bo", skills=["Java"])

    res = score_candidate_full(job, cand, requirements={"must_have_skills": ["Python"]})

    assert res.passed_knockouts is False
    assert res.breakdown.overall_score == 0.0
    assert any("missing required skill: Python" in r for r in res.knockout_reasons)
    assert provider.calls == []   # the LLM is never invoked for a knocked-out candidate


def test_per_job_weights_change_the_overall(make_gateway):
    make_gateway(RecordingProvider(text=_RUBRIC))
    job = ParsedJob(title="Eng")
    cand = CandidateProfile(full_name="Ada", skills=["Python"])

    # default 40/30/15/15, location unknown (0.5) → 69.5
    assert score_candidate_full(job, cand).breakdown.overall_score == 69.5

    # all weight on skill_match (0.8) → 80.0
    skill_only = score_candidate_full(
        job, cand,
        weights={"skill_match": 1.0, "experience_fit": 0, "tech_stack_overlap": 0, "location_match": 0},
    )
    assert skill_only.breakdown.overall_score == 80.0


def test_threshold_is_per_job(make_gateway):
    make_gateway(RecordingProvider(text=_RUBRIC))
    job = ParsedJob(title="Eng")
    cand = CandidateProfile(full_name="Ada", skills=["Python"])

    assert score_candidate_full(job, cand, threshold=90).below_threshold is True
    assert score_candidate_full(job, cand, threshold=50).below_threshold is False


def test_remote_job_gives_full_location_match(make_gateway):
    make_gateway(RecordingProvider(text=_RUBRIC))
    job = ParsedJob(title="Eng", location=JobLocation(remote=True))
    cand = CandidateProfile(full_name="Ada", skills=["Python"])
    # location_match 1.0 → (.40*.8+.30*.7+.15*.6+.15*1.0)*100 = 77.0
    assert score_candidate_full(job, cand).breakdown.overall_score == 77.0


def test_rescore_is_stable(make_gateway):
    make_gateway(RecordingProvider(text=_RUBRIC))
    job = ParsedJob(title="Eng", skills_required=["Python"])
    cand = CandidateProfile(full_name="Ada", skills=["Python"])
    first = score_candidate_full(job, cand).breakdown.overall_score
    second = score_candidate_full(job, cand).breakdown.overall_score
    assert first == second
