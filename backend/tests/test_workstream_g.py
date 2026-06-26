"""Workstream G — engagement & scheduling hardening.

Proves: every outreach carries an opt-out link and is logged to the candidate
timeline with the acting human; opted-out candidates are excluded from sends;
the reply parser recognises 'reschedule'; confirming an interview slot is a
human-gated action that is recorded and logged.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from db import repository
from services import email_service

_ALL_STORES = (
    "_jobs", "_candidates", "_applications", "_emails", "_generated_jds", "_orgs",
    "_activity_events", "_ai_decisions", "_scorecards", "_consents", "_eeo",
    "_resumes", "_pipeline_stages", "_interviews",
)


@pytest.fixture(autouse=True)
def _force_inmemory_and_clear(monkeypatch):
    monkeypatch.setattr(repository, "db_available", lambda: False)
    # Degrade email to draft-only so tests never hit the real Gmail API.
    monkeypatch.setattr(email_service, "availability", lambda: (False, "disabled in tests"))
    for name in _ALL_STORES:
        getattr(repository, name).clear()
    yield
    for name in _ALL_STORES:
        getattr(repository, name).clear()


def _seed_app(email="cand@x.com"):
    job = repository.create_job({"title": "Eng"})
    c = repository.upsert_candidate({"full_name": "Ada", "email": email})
    a = repository.create_application({"job_id": job["id"], "candidate_id": c["id"]})
    return job, c, a


def _client():
    import main
    return TestClient(main.app)


def test_outreach_includes_opt_out_link_and_is_logged():
    _job, _c, a = _seed_app()
    client = _client()
    r = client.post(f"/api/applications/{a['id']}/send-email",
                    json={"decision": "proceed", "subject": "Hi", "body": "Hello there"})
    assert r.status_code == 200
    assert "/opt-out" in r.json()["opt_out_url"]

    emails = repository.list_emails_for_application(a["id"])
    assert any("/opt-out" in (e.get("body") or "") for e in emails)   # link in the message

    timeline = client.get(f"/api/applications/{a['id']}/timeline").json()["timeline"]
    sent = [i for i in timeline if i.get("type") == "event" and i.get("action") == "outreach.sent"]
    assert sent and sent[0]["actor_type"] == "human"
    assert any(i.get("type") == "email" for i in timeline)


def test_opted_out_candidate_excluded_from_sends():
    _job, _c, a = _seed_app()
    client = _client()
    # candidate clicks the opt-out link
    assert client.get(f"/api/applications/{a['id']}/opt-out").status_code == 200
    # subsequent send is refused
    r = client.post(f"/api/applications/{a['id']}/send-email",
                    json={"decision": "proceed", "subject": "Hi", "body": "Hello"})
    assert r.status_code == 409


def test_opt_out_records_event():
    _job, c, a = _seed_app()
    _client().get(f"/api/applications/{a['id']}/opt-out")
    assert repository.get_candidate(c["id"])["opted_out"] is True
    events = repository.list_activity_events("candidate", c["id"])
    assert any(e["action"] == "candidate.opted_out" for e in events)


def test_reply_parser_recognises_reschedule(make_gateway):
    from agents.response_parser import classify_reply
    from .conftest import RecordingProvider

    make_gateway(RecordingProvider(text=json.dumps(
        {"intent": "reschedule", "confidence": 0.9, "summary": "wants a later time", "follow_up_needed": True}
    )))
    res = classify_reply("I'm interested but can we do next week instead?")
    assert res.intent == "reschedule"


def test_confirm_slot_is_human_gated_and_logged():
    _job, _c, a = _seed_app()
    client = _client()

    slots = client.get(f"/api/applications/{a['id']}/interview/slots")
    assert slots.status_code == 200 and len(slots.json()["slots"]) > 0

    # interviewer role lacks interview.schedule permission
    blocked = client.post(
        f"/api/applications/{a['id']}/interview/confirm",
        json={"scheduled_at": "2026-07-01T10:00:00+00:00"},
        headers={"X-User-Role": "interviewer"},
    )
    assert blocked.status_code == 403

    # recruiter confirms (human action)
    ok = client.post(
        f"/api/applications/{a['id']}/interview/confirm",
        json={"scheduled_at": "2026-07-01T10:00:00+00:00", "stage": "Technical",
              "format": "Video", "interviewer_email": "panel@co.com"},
        headers={"X-User-Role": "recruiter"},
    )
    assert ok.status_code == 200
    assert len(repository.list_interviews_for_application(a["id"])) == 1

    timeline = client.get(f"/api/applications/{a['id']}/timeline").json()["timeline"]
    assert any(i.get("action") == "interview.scheduled" for i in timeline)
