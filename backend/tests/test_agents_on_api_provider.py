"""The existing vertical slice (JD parse, scoring, outreach, reply parsing)
runs end-to-end through the gateway on the API provider.

Uses the RecordingProvider (which masquerades as the anthropic_api provider)
so the full agent → gateway → provider path is exercised without network access.
"""

from __future__ import annotations

import json

from agents.jd_parser import parse_job
from agents.outreach import draft_decision_email
from agents.response_parser import classify_reply
from agents.scoring import score_candidate
from models.candidate import CandidateProfile
from models.job import ParsedJob

from .conftest import RecordingProvider


def test_jd_parser_validates_parsed_job(make_gateway):
    payload = {
        "title": "Senior Backend Engineer",
        "skills_required": ["Python", "FastAPI"],
        "skills_nice_to_have": ["Kubernetes"],
        "seniority": "senior",
        "location": {"city": "Pune", "country": "India", "remote": True},
        "salary_range": None,
        "responsibilities": ["Build APIs"],
        "tech_stack": ["Postgres"],
    }
    make_gateway(RecordingProvider(text=json.dumps(payload)))
    job = parse_job("raw linkedin post text")
    assert isinstance(job, ParsedJob)
    assert job.title == "Senior Backend Engineer"
    assert job.seniority == "senior"
    assert job.location.remote is True


def test_scoring_validates_ats_breakdown(make_gateway):
    breakdown = {
        "skill_match": 0.8,
        "experience_fit": 0.7,
        "location_match": 1.0,
        "tech_stack_overlap": 0.6,
        "overall_score": 74.5,
        "reasoning": "Strong Python overlap.",
    }
    provider = RecordingProvider(text=json.dumps(breakdown))
    make_gateway(provider)

    job = ParsedJob(title="Backend Engineer", skills_required=["Python"])
    candidate = CandidateProfile(full_name="Ada Lovelace", skills=["Python", "FastAPI"])
    result = score_candidate(job, candidate)

    assert result.overall_score == 74.5
    assert result.skill_match == 0.8
    # The scoring prompt was the one used.
    assert "ATS scoring engine" in provider.calls[-1]["system"]


def test_reply_classification(make_gateway):
    payload = {
        "intent": "interested",
        "confidence": 0.9,
        "summary": "Candidate is keen to proceed.",
        "follow_up_needed": False,
    }
    make_gateway(RecordingProvider(text=json.dumps(payload)))
    intent = classify_reply("Yes, I'd love to move forward!")
    assert intent.intent == "interested"
    assert intent.confidence == 0.9


def test_outreach_uses_fast_model(make_gateway):
    payload = {"subject": "You've advanced", "body": "Hi Ada, great news..."}
    provider = RecordingProvider(text=json.dumps(payload))
    make_gateway(provider, default_model="sonnet")

    draft = draft_decision_email(
        "proceed",
        {"full_name": "Ada Lovelace", "skills": ["Python"], "headline": "Engineer"},
        "Backend Engineer",
    )
    assert draft.subject == "You've advanced"
    # Email drafting is pinned to the fast (haiku) model by the prompt template.
    assert provider.calls[-1]["model"] == "haiku"
