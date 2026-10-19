"""Sensitivity audit of the safety tests.

A safety test is only worth having if it FAILS when the guard it covers is
removed. Each case here disables exactly one guard with a monkeypatch, runs
the original safety test function, and asserts that it now fails; then runs
it again unpatched and asserts that it passes. A safety test that survives
its guard's removal checks nothing — and that is what this module catches.

Covered: the human-in-the-loop rejection gate, in-memory tenant isolation,
RBAC (guard + route), the reserved system identity, and the no-PII
guarantees on graph state. The Postgres RLS verifier has the same kind of
self-check in scripts/verify_rls.py --sensitivity (run in CI).
"""

from __future__ import annotations

from typing import Annotated

import pytest
from _pytest.outcomes import Failed
from pydantic import Field

from db import repository
from services import auth, rbac
from tests import test_auth_guard as t_auth
from tests import test_phase1_scheduling as t_p1
from tests import test_phase2_storage as t_store
from tests import test_rbac as t_rbac
from tests import test_tenant_isolation as t_tenant
from tests import test_workstream_c as t_gate
from orchestrator import graph as graph_mod
from services import email_service, outreach_send
from tests import test_phase2_send as t_send

_ALL_STORES = (
    "_jobs", "_candidates", "_applications", "_emails", "_generated_jds", "_orgs",
    "_activity_events", "_ai_decisions", "_scorecards", "_consents", "_eeo",
    "_resumes", "_pipeline_stages", "_interviews",
)


@pytest.fixture(autouse=True)
def _same_world_as_the_safety_tests(monkeypatch):
    """The originals run under autouse fixtures we bypass by calling them
    directly; reproduce that world here (in-memory store, cleared)."""
    monkeypatch.setattr(repository, "db_available", lambda: False)
    for name in _ALL_STORES:
        getattr(repository, name).clear()
    yield
    for name in _ALL_STORES:
        getattr(repository, name).clear()


def _clear_stores():
    for name in _ALL_STORES:
        getattr(repository, name).clear()


def _must_fail(test_fn, *args):
    """The original test must NOT pass with the guard removed: an assertion,
    pytest's DID NOT RAISE, or any error raised by a remaining layer (e.g. the
    unique index behind a removed ledger lookup) all count — a test that errors
    does not pass. _must_pass is what proves it passes with the guard intact."""
    _clear_stores()
    with pytest.raises((Exception, Failed)):
        test_fn(*args)


def _must_pass(test_fn, *args):
    _clear_stores()
    test_fn(*args)


# ── human-in-the-loop rejection gate ──────────────────────────────────
def test_human_gate_tests_fail_when_the_gate_is_removed(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(repository, "is_human_principal", lambda: True)   # every caller "is human"
        _must_fail(t_gate.test_unattended_context_cannot_reject)
        _must_fail(t_gate.test_ai_can_score_but_not_reject)
    _must_pass(t_gate.test_unattended_context_cannot_reject)
    _must_pass(t_gate.test_ai_can_score_but_not_reject)


def test_human_gate_audit_test_fails_when_actor_attribution_is_broken(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(repository, "current_user_id", lambda: "nobody")   # audit rows lose the actor
        _must_fail(t_gate.test_human_reject_is_audited_with_human_actor)
    _must_pass(t_gate.test_human_reject_is_audited_with_human_actor)


# ── in-memory tenant isolation ─────────────────────────────────────────
def test_tenant_isolation_tests_fail_when_the_filter_is_removed(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(repository, "_owned", lambda rec: True)             # every row visible to every tenant
        _must_fail(t_tenant.test_jobs_isolated_by_tenant)
        _must_fail(t_tenant.test_applications_and_candidates_isolated)
        _must_fail(t_tenant.test_export_and_delete_are_tenant_scoped)
    _must_pass(t_tenant.test_jobs_isolated_by_tenant)
    _must_pass(t_tenant.test_applications_and_candidates_isolated)
    _must_pass(t_tenant.test_export_and_delete_are_tenant_scoped)


# ── RBAC ───────────────────────────────────────────────────────────────
def test_rbac_tests_fail_when_permissions_are_granted_to_everyone(monkeypatch):
    with monkeypatch.context() as m:
        # Mutate the guard at its source (the matrix), not the function name:
        # test_rbac imports has_permission directly, so patching the attribute
        # would leave the original test calling the real function.
        m.setattr(rbac, "PERMISSIONS", {perm: set(rbac.ROLES) for perm in rbac.PERMISSIONS})
        _must_fail(t_rbac.test_permission_matrix)
        _must_fail(t_rbac.test_require_permission_blocks_then_allows)
        _must_fail(t_rbac.test_admin_route_enforces_role)             # 403 becomes 200
    _must_pass(t_rbac.test_permission_matrix)
    _must_pass(t_rbac.test_require_permission_blocks_then_allows)
    _must_pass(t_rbac.test_admin_route_enforces_role)


# ── reserved system identity over HTTP ─────────────────────────────────
def test_system_role_tests_fail_when_the_role_becomes_claimable(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(auth, "ROLES", (*rbac.ROLES, "system"))              # resolver would hand out 'system'
        _must_fail(t_p1.test_role_header_cannot_resolve_to_the_system_principal, "system")
    _must_pass(t_p1.test_role_header_cannot_resolve_to_the_system_principal, "system")


def test_reserved_user_id_tests_fail_when_the_namespace_is_not_reserved(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(auth, "SYSTEM_USER_PREFIX", "zzz:")                  # 'system:*' no longer refused
        _must_fail(t_p1.test_reserved_system_user_id_is_refused, "system:scheduler")
    _must_pass(t_p1.test_reserved_system_user_id_is_refused, "system:scheduler")


def test_startup_guard_tests_fail_when_the_guard_is_disabled(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(auth, "is_dev_like", lambda env: True)               # every env counts as dev
        _must_fail(t_auth.test_trusted_headers_refused_outside_dev, m, "prod", [])
    _must_pass(t_auth.test_trusted_headers_refused_outside_dev, monkeypatch, "prod", [])


# ── no-PII guarantees on graph state ───────────────────────────────────
def test_structural_no_pii_test_fails_when_a_free_text_field_is_added(monkeypatch):
    class Leaky(t_store.ThreadState):
        note: str                                                       # free text in graph state

    with monkeypatch.context() as m:
        m.setattr(t_store, "ThreadState", Leaky)
        _must_fail(t_store.test_state_schema_is_structurally_free_of_free_text)
    _must_pass(t_store.test_state_schema_is_structurally_free_of_free_text)


def test_structural_no_pii_test_fails_when_an_id_mark_lacks_its_format_constraint(monkeypatch):
    class LooselyMarked(t_store.ThreadState):
        alias: Annotated[str, t_store.IdMarker()]                       # marked, but unconstrained

    with monkeypatch.context() as m:
        m.setattr(t_store, "ThreadState", LooselyMarked)
        _must_fail(t_store.test_state_schema_is_structurally_free_of_free_text)
    _must_pass(t_store.test_state_schema_is_structurally_free_of_free_text)


def test_detector_layer_flags_each_pii_class():
    hits = t_store._detector_hits('{"a": "ada@x.com", "b": "+91 98765 43210", "c": "a veteran", "d": "call me later"}')
    kinds = {h.split(":", 1)[0] for h in hits}
    assert kinds == {"email", "phone", "affinity", "prose"}
    assert t_store._detector_hits('{"a": "evt:7", "b": "4b1c0d4e-1111-4e8b-9b3a-000000000001"}') == []


def test_runtime_id_validation_test_fails_when_the_pattern_is_dropped(monkeypatch):
    class Unconstrained(t_store.ThreadState):
        started_by: Annotated[str, t_store.IdMarker(), Field(pattern=r"^.*$")]  # anything goes

    with monkeypatch.context() as m:
        m.setattr(t_store, "ThreadState", Unconstrained)
        _must_fail(t_store.test_id_marked_fields_reject_non_id_values_at_runtime, "Ada Lovelace")
    _must_pass(t_store.test_id_marked_fields_reject_non_id_values_at_runtime, "Ada Lovelace")


# ── Phase 2 / PR 3: the send gate's guards (same-PR rule) ──────────────
def test_send_idempotency_test_fails_when_the_ledger_lookup_is_removed(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(repository, "get_email_by_idempotency_key", lambda key: None)   # every send looks new
        _must_fail(t_send.test_same_key_never_sends_twice)
    _must_pass(t_send.test_same_key_never_sends_twice)


def test_unknown_status_test_fails_when_ambiguity_is_classified_as_definite(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(email_service, "classify_send_error", lambda exc: email_service.DefiniteSendError)
        m.setattr(email_service, "AmbiguousSendError", email_service.DefiniteSendError)  # 5xx treated as "not sent"
        _must_fail(t_send.test_ambiguous_errors_mark_unknown_not_failed)
    _must_pass(t_send.test_ambiguous_errors_mark_unknown_not_failed)


def test_resolution_rule_test_fails_when_resolution_is_skipped(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(outreach_send, "resolve_unresolved", lambda application_id, to: [])  # re-approval mints blindly
        _must_fail(t_send.test_reapproval_resolves_previous_attempt_before_minting)
    _must_pass(t_send.test_reapproval_resolves_previous_attempt_before_minting)


def test_forged_envelope_security_event_test_fails_when_classification_is_removed(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(graph_mod, "_classify", lambda envelope, expected: "stale")        # everything looks stale
        _must_fail(t_send.test_forged_envelope_writes_a_security_event, "bare_payload")
    _must_pass(t_send.test_forged_envelope_writes_a_security_event, "bare_payload")


def test_human_only_send_approval_test_fails_when_the_check_is_removed(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(graph_mod, "_require_human", lambda action: None)                  # the system may approve
        _must_fail(t_send.test_system_cannot_approve_send)
    _must_pass(t_send.test_system_cannot_approve_send)


def test_index_lag_rule_test_fails_when_the_lookup_is_not_retried(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(outreach_send, "LOOKUP_ATTEMPTS", 1)                                # one lookup, no retry
        _must_fail(t_send.test_lookup_misses_then_finds_on_retry_and_nothing_is_sent_twice)
    _must_pass(t_send.test_lookup_misses_then_finds_on_retry_and_nothing_is_sent_twice)


def test_index_lag_rule_test_fails_when_the_age_gate_is_removed(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(outreach_send, "MIN_UNKNOWN_AGE_SECONDS", 0)                        # a young negative counts
        _must_fail(t_send.test_negative_lookup_on_a_young_unknown_attempt_does_not_mint_a_new_key)
    _must_pass(t_send.test_negative_lookup_on_a_young_unknown_attempt_does_not_mint_a_new_key)
