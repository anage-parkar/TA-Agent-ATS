"""Phase 2 / PR 3 — the send gate.

Ledger-first idempotent send (key committed before the provider call, same
key never sends twice), ambiguous failures become `unknown` (never `failed`),
a re-approval resolves the previous attempt before minting a key, the
combined "proceed and send" action records two approvals with one send, the
system cannot approve a send, and forged resume envelopes raise a security
event while stale ones do not.

Every test builds its own hermetic world (no pytest fixtures) so the
sensitivity module can call these functions directly with one guard removed.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest
from langgraph.types import Command

from db import repository
from db.repository import HumanActorRequired
from orchestrator import checkpointer as cp
from orchestrator import graph as graph_mod
from orchestrator import runner
from orchestrator.state import ScoreSummary
from services import email_service, outreach_send, security_events
from services.config import settings
from services.tenant_context import use_system, use_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
R1 = "11111111-1111-4111-8111-111111111111"
R2 = "22222222-2222-4222-8222-222222222222"
APPROVE = {"approve": True, "subject": "Next steps", "body": "Hello there"}

_ALL_STORES = (
    "_jobs", "_candidates", "_applications", "_emails", "_generated_jds", "_orgs",
    "_activity_events", "_ai_decisions", "_scorecards", "_consents", "_eeo",
    "_resumes", "_pipeline_stages", "_interviews",
)


class FakeGmail:
    """Stands in for email_service.send_email / find_sent_message.

    `mode` controls the next send: ok | ambiguous | definite | timeout (a raw
    TimeoutError, classified by the ledger). `in_mailbox_ids` / `in_mailbox_keys`
    are what lookups find. `before_send` is called with the key before the
    provider 'answers', so tests can assert the ledger row already exists."""

    def __init__(self):
        self.mode = "ok"
        self.sent: list[dict] = []
        self.lookups: list[dict] = []
        self.in_mailbox_ids: set[str] = set()
        self.in_mailbox_keys: set[str] = set()
        self.before_send = None

    def send_email(self, to, subject, body, decision="proceed", opt_out_url=None, *,
                   message_id=None, idempotency_key=None):
        if self.before_send:
            self.before_send(idempotency_key)
        self.sent.append({"to": to, "subject": subject, "message_id": message_id,
                          "idempotency_key": idempotency_key, "mode": self.mode})
        if self.mode == "ambiguous":
            raise email_service.AmbiguousSendError("HttpError: <HttpError 503>")
        if self.mode == "definite":
            raise email_service.DefiniteSendError("HttpError: <HttpError 400>")
        if self.mode == "timeout":
            raise TimeoutError("read timed out")
        return {"thread_id": f"t-{len(self.sent)}", "message_id": f"g-{len(self.sent)}"}

    def find_sent_message(self, *, message_id=None, idempotency_key=None, to=None, days=7):
        self.lookups.append({"message_id": message_id, "idempotency_key": idempotency_key, "to": to})
        if message_id and message_id in self.in_mailbox_ids:
            return {"message_id": "g-found", "thread_id": "t-found", "matched_by": "message_id"}
        if idempotency_key and idempotency_key in self.in_mailbox_keys:
            return {"message_id": "g-found", "thread_id": "t-found", "matched_by": "idempotency_header"}
        return None


def _clear():
    for name in _ALL_STORES:
        getattr(repository, name).clear()


@contextlib.contextmanager
def _world():
    """Hermetic: in-memory store + checkpointer, fake mailbox, fake scorer,
    captured security events. Undone on exit."""
    mp = pytest.MonkeyPatch()
    gmail = FakeGmail()
    captured: list[dict] = []
    mp.setattr(repository, "db_available", lambda: False)
    mp.setattr(settings, "app_env", "test")
    mp.setattr(settings, "checkpointer_backend", "memory")
    mp.setattr(settings, "outreach_from_email", "recruiting@parkar.in")
    mp.setattr(email_service, "send_email", gmail.send_email)
    mp.setattr(email_service, "find_sent_message", gmail.find_sent_message)
    mp.setattr(graph_mod, "score_application",
               lambda a: ScoreSummary(overall=72.0, passed_knockouts=True, below_threshold=False))
    security_events.clear_sinks()
    security_events.register_sink(captured.append)
    cp.reset_checkpointer()
    graph_mod.reset_graph()
    _clear()
    try:
        yield gmail, captured
    finally:
        mp.undo()
        security_events.clear_sinks()
        cp.reset_checkpointer()
        graph_mod.reset_graph()
        _clear()


def _seed(opted_out: bool = False) -> dict:
    with use_tenant(A):
        job = repository.create_job({"title": "Eng"})
        cand = repository.upsert_candidate({"full_name": "Ada", "email": "ada@candidate.example"})
        if opted_out:
            repository.set_candidate_opt_out(cand["id"], True)
        app = repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})
        repository._applications[app["id"]]["orchestrated"] = True
    return app


def _to_send_gate(app: dict) -> None:
    with use_tenant(A, user_id=R1):
        runner.start_thread(app["id"])
        assert runner.resume_thread(app["id"], {"decision": "proceed", "event_id": "evt:1"})["waiting_on"] == "send_gate"


def _events(action: str, entity_id: str) -> list[dict]:
    return [e for e in repository._activity_events.values()
            if e.get("action") == action and e.get("entity_id") == entity_id]


def _outbound(app_id: str) -> list[dict]:
    return sorted([e for e in repository._emails.values()
                   if e.get("application_id") == app_id and e.get("direction") == "outbound"],
                  key=lambda e: e.get("created_at") or "")


# ── ledger-first send ─────────────────────────────────────────────────
def test_key_is_committed_before_the_provider_call():
    with _world() as (gmail, _):
        app = _seed()
        seen = {}
        gmail.before_send = lambda key: seen.update(row=dict(repository.get_email_by_idempotency_key(key) or {}))
        gmail.mode = "ambiguous"
        with use_tenant(A, user_id=R1):
            res = outreach_send.send_for_application(app["id"], event_id="evt:9", subject="S", body="B")
        assert seen["row"] is not None and seen["row"]["send_status"] == "pending"  # existed before Gmail answered
        assert seen["row"]["message_id"] == outreach_send.deterministic_message_id(seen["row"]["idempotency_key"])
        assert res.action == "unknown" and res.email["send_status"] == "unknown"


def test_same_key_never_sends_twice():
    with _world() as (gmail, _):
        app = _seed()
        key = outreach_send.idempotency_key(app["id"], "send_gate", "evt:1")
        with use_tenant(A, user_id=R1):
            first = outreach_send.send_with_ledger(application_id=app["id"], to="ada@candidate.example",
                                                  subject="S", body="B", key=key)
            second = outreach_send.send_with_ledger(application_id=app["id"], to="ada@candidate.example",
                                                   subject="S", body="B", key=key)
        assert first.action == "sent" and second.action == "already_sent"
        assert second.email["id"] == first.email["id"]
        assert len(gmail.sent) == 1                                 # the provider was called once
        assert len(_outbound(app["id"])) == 1                       # one ledger row


def test_ambiguous_errors_mark_unknown_not_failed():
    with _world() as (gmail, _):
        app = _seed()
        with use_tenant(A, user_id=R1):
            gmail.mode = "timeout"                                  # a raw exception, classified by the ledger
            res = outreach_send.send_for_application(app["id"], event_id="evt:a", subject="S", body="B")
            assert res.action == "unknown" and res.email["send_status"] == "unknown"
            gmail.mode = "ambiguous"
            res = outreach_send.send_for_application(app["id"], event_id="evt:b", subject="S", body="B")
            assert res.email["send_status"] == "unknown"
            gmail.mode = "definite"
            res = outreach_send.send_for_application(app["id"], event_id="evt:c", subject="S", body="B")
            assert res.action == "failed" and res.email["send_status"] == "failed"


def test_unknown_attempt_is_resolved_by_lookup_before_any_resend():
    with _world() as (gmail, _):
        app = _seed()
        key = outreach_send.idempotency_key(app["id"], "send_gate", "evt:1")
        with use_tenant(A, user_id=R1):
            gmail.mode = "ambiguous"
            first = outreach_send.send_with_ledger(application_id=app["id"], to="ada@candidate.example",
                                                  subject="S", body="B", key=key)
            assert first.email["send_status"] == "unknown"
            # It had gone out after all: the lookup finds the Message-ID → sent, no second send.
            gmail.mode = "ok"
            gmail.in_mailbox_ids.add(first.email["message_id"])
            res = outreach_send.send_with_ledger(application_id=app["id"], to="ada@candidate.example",
                                                 subject="S", body="B", key=key)
            assert res.action == "resolved_sent" and res.email["send_status"] == "sent"
            assert len(gmail.sent) == 1 and gmail.lookups[-1]["message_id"] == first.email["message_id"]


def test_unknown_attempt_not_in_mailbox_is_resent_under_the_same_key():
    with _world() as (gmail, _):
        app = _seed()
        key = outreach_send.idempotency_key(app["id"], "send_gate", "evt:1")
        with use_tenant(A, user_id=R1):
            gmail.mode = "ambiguous"
            first = outreach_send.send_with_ledger(application_id=app["id"], to="ada@candidate.example",
                                                  subject="S", body="B", key=key)
            gmail.mode = "ok"
            res = outreach_send.send_with_ledger(application_id=app["id"], to="ada@candidate.example",
                                                 subject="S", body="B", key=key)
        assert res.action == "sent" and res.email["id"] == first.email["id"]
        assert [s["message_id"] for s in gmail.sent] == [first.email["message_id"]] * 2  # same deterministic id
        assert len(_outbound(app["id"])) == 1


# ── the gate: re-approval resolves first ──────────────────────────────
def test_reapproval_resolves_previous_attempt_before_minting():
    with _world() as (gmail, _):
        app = _seed()
        _to_send_gate(app)
        with use_tenant(A, user_id=R2):
            gmail.mode = "ambiguous"
            status = runner.resume_thread(app["id"], {**APPROVE, "event_id": "evt:2"})
        assert status["waiting_on"] == "send_gate"                  # unknown → parked at the gate again
        first = _outbound(app["id"])[0]
        assert first["send_status"] == "unknown"
        assert repository._applications[app["id"]]["stage"] == "Reviewed"

        # The earlier approval's email had in fact gone out.
        gmail.mode = "ok"
        gmail.in_mailbox_ids.add(first["message_id"])
        with use_tenant(A, user_id=R2):
            status = runner.resume_thread(app["id"], {**APPROVE, "event_id": "evt:3"})
        assert status["waiting_on"] == "await_reply"
        rows = _outbound(app["id"])
        assert len(rows) == 1 and rows[0]["send_status"] == "sent"   # no new key minted
        assert len(gmail.sent) == 1                                  # never two emails for one candidate
        assert gmail.lookups and gmail.lookups[-1]["message_id"] == first["message_id"]
        assert repository._applications[app["id"]]["stage"] == "Outreach"


def test_reapproval_after_a_confirmed_failure_mints_a_new_key():
    with _world() as (gmail, _):
        app = _seed()
        _to_send_gate(app)
        with use_tenant(A, user_id=R2):
            gmail.mode = "ambiguous"
            runner.resume_thread(app["id"], {**APPROVE, "event_id": "evt:2"})
            gmail.mode = "ok"                                        # mailbox empty: the lookup finds nothing
            status = runner.resume_thread(app["id"], {**APPROVE, "event_id": "evt:3"})
        assert status["waiting_on"] == "await_reply"
        rows = _outbound(app["id"])
        assert [r["send_status"] for r in rows] == ["failed", "sent"]
        assert rows[0]["idempotency_key"] != rows[1]["idempotency_key"]
        assert len(gmail.sent) == 2


def test_system_cannot_approve_send():
    with _world() as (gmail, _):
        app = _seed()
        _to_send_gate(app)
        with use_system(A, actor="orchestrator"):
            with pytest.raises(HumanActorRequired):
                runner.resume_thread(app["id"], {**APPROVE, "event_id": "evt:s"})
        assert gmail.sent == [] and _outbound(app["id"]) == []
        with use_tenant(A, user_id=R1):
            assert runner.thread_status(app["id"])["waiting_on"] == "send_gate"


def test_opted_out_candidate_is_refused_at_the_gate():
    with _world() as (gmail, _):
        app = _seed(opted_out=True)
        _to_send_gate(app)
        with use_tenant(A, user_id=R2):
            with pytest.raises(outreach_send.OutreachRefused):
                runner.resume_thread(app["id"], {**APPROVE, "event_id": "evt:o"})
            assert runner.thread_status(app["id"])["waiting_on"] == "send_gate"
        assert gmail.sent == [] and _outbound(app["id"]) == []


# ── combined action: two approvals, one send ──────────────────────────
def test_combined_action_records_two_approvals_with_one_send():
    from fastapi.testclient import TestClient
    import main

    with _world() as (gmail, _):
        app = _seed()
        with use_tenant(A, user_id=R1):
            runner.start_thread(app["id"])
        r = TestClient(main.app).post(
            f"/api/candidates/{app['id']}/decision",
            json={"decision": "proceed", "send": {"approve": True, "subject": "Next steps", "body": "Hello"}},
            headers={"X-Tenant-Id": A, "X-User-Id": R2, "X-User-Role": "recruiter"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["thread"]["waiting_on"] == "await_reply" and body["stage"] == "Outreach"
        group = body["action_group_id"]
        decided = _events("application.decided", app["id"])
        approved = _events("outreach.approved", app["id"])
        assert len(decided) == 1 and len(approved) == 1
        assert decided[0]["metadata"]["action_group_id"] == group == approved[0]["metadata"]["action_group_id"]
        assert decided[0]["actor_id"] == approved[0]["actor_id"] == R2
        assert len(gmail.sent) == 1
        assert gmail.sent[0]["idempotency_key"].startswith(f"idem:{app['id']}:send_gate:evt:")


def test_combined_action_is_refused_for_non_orchestrated_applications():
    from fastapi.testclient import TestClient
    import main

    with _world() as (gmail, _):
        app = _seed()
        repository._applications[app["id"]]["orchestrated"] = False
        r = TestClient(main.app).post(
            f"/api/candidates/{app['id']}/decision",
            json={"decision": "proceed", "send": {"subject": "S", "body": "B"}},
            headers={"X-Tenant-Id": A, "X-User-Id": R2, "X-User-Role": "recruiter"},
        )
        assert r.status_code == 409 and gmail.sent == []
        assert repository._applications[app["id"]]["status"] != "approved"   # nothing applied


# ── forged vs stale envelopes ─────────────────────────────────────────
@pytest.mark.parametrize("forge", ["bare_payload", "cross_tenant_principal"])
def test_forged_envelope_writes_a_security_event(forge):
    with _world() as (gmail, captured):
        app = _seed()
        with use_tenant(A, user_id=R1):
            runner.start_thread(app["id"])
            p = runner._principal()
            g = graph_mod.get_graph()
            if forge == "bare_payload":
                planted = {"decision": "proceed"}
            else:
                _, planted = runner._envelope(p, {"decision": "proceed"})
                planted["principal"] = {"tenant_id": B, "user_id": R1, "role": "admin"}
            with pytest.raises(graph_mod.ResumeOriginMismatch):          # parked, no runner cleanup
                g.invoke(Command(resume=planted), runner._config(app["id"], p, "rsm:crashed"))

        with use_tenant(A, user_id=R2):
            with pytest.raises(graph_mod.ResumeOriginMismatch) as exc:
                runner.resume_thread(app["id"], {"decision": "proceed", "event_id": "evt:h"})
        assert exc.value.kind == "forged"
        sec = [e for e in captured if e["event"] == "orchestrator.resume_envelope_rejected"]
        assert len(sec) == 1 and sec[0]["severity"] == "critical"
        assert sec[0]["application_id"] == app["id"] and sec[0]["actor_id"] == R2
        assert _events("orchestrator.forged_resume_rejected", app["id"])
        assert repository._applications[app["id"]]["status"] != "approved"   # nothing applied, no retry


def test_stale_envelope_after_a_crash_is_not_a_security_event():
    with _world() as (gmail, captured):
        app = _seed()
        with use_tenant(A, user_id=R1):
            runner.start_thread(app["id"])
        with use_system(A, actor="orchestrator"):                        # legitimate earlier call, worker died
            p = runner._principal()
            rid, envelope = runner._envelope(p, {"decision": "reject", "event_id": "evt:stale"})
            with pytest.raises(HumanActorRequired):
                graph_mod.get_graph().invoke(Command(resume=envelope), runner._config(app["id"], p, rid))
        with use_tenant(A, user_id=R2):
            status = runner.resume_thread(app["id"], {"decision": "proceed", "event_id": "evt:h"})
        assert status["waiting_on"] == "send_gate"
        assert captured == []                                             # stale: audited, not a security event
        assert _events("orchestrator.stale_resume_discarded", app["id"])


# ── headers and error classification ─────────────────────────────────
def test_message_carries_deterministic_message_id_and_idempotency_header():
    with _world():
        key = f"idem:{uuid.uuid4()}:send_gate:evt:1"
        mid = outreach_send.deterministic_message_id(key)
        assert mid == outreach_send.deterministic_message_id(key)            # pure function of the key
        assert mid.startswith("<ta-") and mid.endswith("@parkar.in>")
        msg = email_service.build_message("ada@candidate.example", "S", "B", message_id=mid, idempotency_key=key)
        assert msg["Message-ID"] == mid and msg["X-TA-Idempotency-Key"] == key
        assert msg["to"] == "ada@candidate.example"


def test_classify_send_error():
    import httplib2
    from googleapiclient.errors import HttpError

    def http(status):
        return HttpError(resp=httplib2.Response({"status": status}), content=b"{}")

    assert email_service.classify_send_error(http(503)) is email_service.AmbiguousSendError
    assert email_service.classify_send_error(http(500)) is email_service.AmbiguousSendError
    assert email_service.classify_send_error(http(400)) is email_service.DefiniteSendError
    assert email_service.classify_send_error(http(403)) is email_service.DefiniteSendError
    assert email_service.classify_send_error(TimeoutError()) is email_service.AmbiguousSendError
    assert email_service.classify_send_error(RuntimeError("?")) is email_service.AmbiguousSendError


def test_legacy_send_route_is_unchanged():
    """Non-orchestrated applications keep the direct path: decision applied,
    stored draft when the mailbox is not configured, no ledger columns."""
    from fastapi.testclient import TestClient
    import main

    with _world() as (gmail, _):
        mp = pytest.MonkeyPatch()
        mp.setattr(email_service, "availability", lambda: (False, "disabled in tests"))
        try:
            app = _seed()
            repository._applications[app["id"]]["orchestrated"] = False
            r = TestClient(main.app).post(f"/api/applications/{app['id']}/send-email",
                                          json={"decision": "proceed", "subject": "Hi", "body": "Hello"},
                                          headers={"X-Tenant-Id": A, "X-User-Id": R1, "X-User-Role": "recruiter"})
            assert r.status_code == 200 and r.json()["sent"] is False
            rows = _outbound(app["id"])
            assert len(rows) == 1 and rows[0].get("send_status") is None and rows[0].get("idempotency_key") is None
            assert gmail.sent == []
        finally:
            mp.undo()
