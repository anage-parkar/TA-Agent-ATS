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
    """The original test must fail (an assertion or pytest's DID NOT RAISE)."""
    _clear_stores()
    with pytest.raises((AssertionError, Failed)):
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
