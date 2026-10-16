"""Phase 2 / PR 1 — checkpoint storage: tenant-scoped savers, explicit backend
selection, lifecycle parity (export / erasure), the pinned-package-vs-vendored
DDL check, and the no-PII guarantee on graph state (structural + runtime
format validation + a serialised-payload detector).

No graph exists yet; nothing here exercises a request path except the
lifecycle hooks the repository already exposes.
"""

from __future__ import annotations

import base64
import importlib.metadata
import json
import pathlib
import re
import types
import typing
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from db import repository
from orchestrator import checkpointer as cp
from orchestrator.state import ID_PATTERN, UUID_RE, Decision, IdMarker, ThreadState, _Strict, is_id
from services import redaction
from services.config import settings
from services.tenant_context import use_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
U1 = "4b1c0d4e-1111-4e8b-9b3a-000000000001"
U2 = "4b1c0d4e-2222-4e8b-9b3a-000000000002"
U3 = "4b1c0d4e-3333-4e8b-9b3a-000000000003"
ROOT = pathlib.Path(__file__).resolve().parents[2]

_ALL_STORES = (
    "_jobs", "_candidates", "_applications", "_emails", "_generated_jds", "_orgs",
    "_activity_events", "_ai_decisions", "_scorecards", "_consents", "_eeo",
    "_resumes", "_pipeline_stages", "_interviews",
)


@pytest.fixture(autouse=True)
def _memory_everything(monkeypatch):
    monkeypatch.setattr(repository, "db_available", lambda: False)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "checkpointer_backend", "memory")
    cp.reset_checkpointer()
    for name in _ALL_STORES:
        getattr(repository, name).clear()
    yield
    cp.reset_checkpointer()
    for name in _ALL_STORES:
        getattr(repository, name).clear()


def _cfg(thread_id: str, ns: str = "") -> dict:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}


def _write_checkpoints(saver, thread_id: str, n: int = 3, ns: str = "") -> None:
    """Write n checkpoints (and one pending write each) to a thread/namespace."""
    for i in range(n):
        ck = empty_checkpoint()
        ck["channel_values"] = {"step": i}
        saved = saver.put(_cfg(thread_id, ns), ck, {"source": "input", "step": i, "parents": {}}, {"step": str(i + 1)})
        saver.put_writes(saved, [("step", i)], task_id=f"task-{i}")


# ── pinned package ↔ vendored migration ───────────────────────────────
def _migration_text() -> str:
    return (ROOT / "supabase" / "migrations" / "0011_checkpointer.sql").read_text(encoding="utf-8")


def test_vendored_migration_matches_the_pinned_package():
    import langgraph.checkpoint.postgres.base as pg_base

    text = _migration_text()
    marked_version = re.search(r"@langgraph-checkpoint-postgres:\s*(\S+)", text).group(1)
    marked_count = int(re.search(r"@langgraph-migrations:\s*(\d+)", text).group(1))
    installed = importlib.metadata.version("langgraph-checkpoint-postgres")

    assert marked_version == installed, "bumping the pin requires a new vendored migration"
    assert marked_count == len(pg_base.MIGRATIONS)
    assert f"generate_series(0, {len(pg_base.MIGRATIONS) - 1})" in text

    reqs = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
    assert f"langgraph-checkpoint-postgres=={installed}" in reqs
    assert "langgraph-checkpoint-redis" not in reqs


def _table_block(text: str, table: str) -> str:
    return re.search(rf"create table if not exists {table} \((.*?)\n\);", text, re.S).group(1)


def test_vendored_ddl_has_every_column_the_package_writes():
    """Every column the package's own DDL defines must exist in our vendored
    tables (plus tenant_id), so the pinned saver's SQL can never hit a missing
    column at runtime."""
    import langgraph.checkpoint.postgres.base as pg_base

    text = _migration_text().lower()
    package_sql = "\n".join(pg_base.MIGRATIONS).lower()
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        block = re.search(rf"create table if not exists {table} \((.*?)\);", package_sql, re.S).group(1)
        columns = [line.strip().split()[0] for line in block.splitlines()
                   if line.strip() and not line.strip().startswith("primary key")]
        ours = _table_block(text, table)
        for col in columns:
            assert re.search(rf"^\s*{col}\s", ours, re.M), f"{table}.{col} missing from 0011"
    assert "task_path" in text  # package migration 9
    assert "revoke all on checkpoint_migrations from app_user" in text


def test_migration_tenant_column_is_not_null_and_policies_have_with_check():
    text = _migration_text().lower()
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        block = _table_block(text, table)
        tenant_line = re.search(r"^\s*tenant_id\s+uuid\s+not null", block, re.M)
        assert tenant_line, f"{table}.tenant_id must be NOT NULL"
        assert "current_setting('app.tenant_id', true)" in block
        assert f"'{table}'" in text  # named in the RLS loop
    policy = re.search(r"create policy tenant_isolation on %i(.*?)\$p\$", text, re.S).group(1)
    assert "using (" in policy and "with check (" in policy
    assert "force row level security" in text


# ── graph state holds references, not PII ─────────────────────────────
_SCALARS = (bool, int, float, datetime)


_UNION_ORIGINS = (typing.Union, types.UnionType)


def _metadata_of(annotation, field) -> list:
    """Constraint metadata for a field: pydantic hoists an outer Annotated's
    items into FieldInfo.metadata; an Annotated nested inside Optional keeps
    its items in the annotation, with Field(...) kwargs wrapped in a FieldInfo."""
    items = list(getattr(field, "metadata", []) or [])
    if typing.get_origin(annotation) is typing.Annotated:
        items += list(typing.get_args(annotation)[1:])
    flat = []
    for m in items:
        flat.append(m)
        if isinstance(m, FieldInfo):
            flat += list(m.metadata)
    return flat


def _is_constrained_id(annotation, field) -> bool:
    """Marked as an id AND carries the runtime ID_PATTERN constraint."""
    meta = _metadata_of(annotation, field)
    marked = any(isinstance(m, IdMarker) for m in meta)
    constrained = any(getattr(m, "pattern", None) == ID_PATTERN for m in meta)
    return marked and constrained


def _check(annotation, path: str, field=None) -> list[str]:
    """Paths of every field that is not a constrained id, enum, number,
    timestamp, boolean, or a nested model/list of those."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        inner = typing.get_args(annotation)[0]
        if inner is str:
            return [] if _is_constrained_id(annotation, field) else [path]
        return _check(inner, path, field)
    if origin in _UNION_ORIGINS:
        return [p for arg in typing.get_args(annotation) if arg is not type(None)
                for p in _check(arg, path, field)]
    if origin is list:
        return _check(typing.get_args(annotation)[0], path + "[]", field)
    if annotation is str:
        return [] if _is_constrained_id(annotation, field) else [path]
    if annotation in _SCALARS or (isinstance(annotation, type) and issubclass(annotation, Enum)):
        return []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        bad = []
        for name, f in annotation.model_fields.items():
            bad += _check(f.annotation, f"{path}.{name}", f)
        return bad
    return [f"{path} ({annotation!r})"]


def test_state_schema_is_structurally_free_of_free_text():
    offenders = _check(ThreadState, "ThreadState")
    assert offenders == [], f"free-text or unknown-typed fields in graph state: {offenders}"


def test_structural_walker_catches_plain_strings_and_unconstrained_id_marks():
    """Test the test: a plain str, an id-marked str WITHOUT the pattern, and a
    dict all register as offenders."""
    class Bad(_Strict):
        note: str
        loosely_marked: Annotated[str, IdMarker()]
        blob: dict
        fine: Annotated[str, IdMarker(), __import__("pydantic").Field(pattern=ID_PATTERN)]

    offenders = _check(Bad, "Bad")
    assert "Bad.note" in offenders
    assert "Bad.loosely_marked" in offenders
    assert any(o.startswith("Bad.blob") for o in offenders)
    assert not any(o.startswith("Bad.fine") for o in offenders)


_BASE = dict(application_id=U1, candidate_id=U2, job_id=U3, tenant_id=A, graph_version=1,
             started_by="system:scheduler", status="running", orchestrated=False)


@pytest.mark.parametrize("value", [
    "Ada Lovelace",             # a name
    "AdaLovelace",              # a name without whitespace: no uuid, no prefix
    "ada.lovelace@x.com",       # an email
    "call the candidate",
    "System:Scheduler",         # prefix must be lowercase
    "evt",                      # prefix without a token
    "",
])
def test_id_marked_fields_reject_non_id_values_at_runtime(value):
    with pytest.raises(ValidationError):
        ThreadState(**{**_BASE, "started_by": value})
    with pytest.raises(ValidationError):
        Decision(gate="review_gate", outcome="reject", by=value, at=datetime.now(timezone.utc), event_id="evt:1")
    assert not is_id(value)


@pytest.mark.parametrize("value", [
    U1, "system:scheduler", "evt:7", "evt:01JAX9Q3K8",
    f"idem:{U1}:send_gate:evt:6",   # the PR-3 idempotency-key shape
])
def test_id_marked_fields_accept_uuids_and_prefixed_ids(value):
    assert ThreadState(**{**_BASE, "started_by": value}).started_by == value
    assert is_id(value)


def test_state_schema_rejects_unknown_fields_and_validates_on_assignment():
    with pytest.raises(ValidationError):
        ThreadState(**{**_BASE, "note": "call candidate tomorrow"})  # extra field forbidden
    state = ThreadState(**_BASE)
    with pytest.raises(ValidationError):
        state.started_by = "Ada Lovelace"                             # validate_assignment


def _timestamp_paths(model, prefix="") -> set[str]:
    """Schema-known timestamp fields (datetime-typed), as dotted paths."""
    found = set()
    for name, f in model.model_fields.items():
        ann = f.annotation
        args = [a for a in typing.get_args(ann) if a is not type(None)] or [ann]
        for a in args:
            if typing.get_origin(a) is list:
                a = typing.get_args(a)[0]
            if a is datetime:
                found.add(f"{prefix}{name}")
            elif isinstance(a, type) and issubclass(a, BaseModel):
                found |= _timestamp_paths(a, f"{prefix}{name}.")
    return found


def _strip(data, model, prefix=""):
    """Drop schema-known timestamp fields from a dumped state (recursively)."""
    ts = _timestamp_paths(model)
    def walk(obj, path):
        if isinstance(obj, dict):
            return {k: walk(v, f"{path}{k}.") for k, v in obj.items() if f"{path}{k}" not in ts}
        if isinstance(obj, list):
            return [walk(v, path) for v in obj]
        return obj
    return walk(data, prefix)


def _detector_hits(payload: str) -> list[str]:
    """PII detectors from services.redaction, with schema-known UUID values
    masked (their hex digit runs look like phone numbers; the format is
    validated separately). Timestamps are removed by the caller."""
    masked = re.sub(UUID_RE, "<uuid>", payload)
    hits = []
    hits += [f"email:{m}" for m in redaction._EMAIL_RE.findall(masked)]
    hits += [f"phone:{m}" for m in redaction._PHONE_RE.findall(masked)]
    hits += [f"affinity:{m}" for m in redaction._AFFINITY_RE.findall(masked)]
    hits += [f"prose:{v}" for v in re.findall(r'"([^"]*)"', masked) if " " in v]
    return hits


def _full_state(**overrides) -> ThreadState:
    return ThreadState(**{**_BASE, "status": "waiting_human", "orchestrated": True,
        "last_event_id": "evt:7",
        "score_summary": {"overall": 72.5, "passed_knockouts": True, "below_threshold": False},
        "decisions": [{"gate": "review_gate", "outcome": "proceed", "by": "system:orchestrator",
                       "at": datetime.now(timezone.utc), "event_id": "evt:6"}],
        "outreach": {"email_id": U2, "idempotency_key": f"idem:{U1}:send_gate:evt:6", "send_status": "pending"},
        **overrides})


def test_serialised_state_trips_no_pii_detector():
    """Second layer: a fully populated state, serialised with its schema-known
    timestamp fields removed, trips none of the redaction detectors (email,
    phone, affinity) and contains no whitespace-bearing string."""
    data = _strip(json.loads(_full_state().model_dump_json()), ThreadState)
    assert "at" not in data["decisions"][0]            # timestamp excluded by schema, not by regex
    assert _detector_hits(json.dumps(data)) == []


def test_detector_layer_is_live():
    """Positive control: a phone number smuggled into a prefixed id (which the
    format pattern cannot exclude) IS caught by the detector layer."""
    state = _full_state(last_event_id="evt:919876543210")
    data = _strip(json.loads(state.model_dump_json()), ThreadState)
    hits = _detector_hits(json.dumps(data))
    assert any(h.startswith("phone:") for h in hits), hits


# ── explicit backend selection ─────────────────────────────────────────
@pytest.mark.parametrize("env, expected", [("dev", "memory"), ("ci", "memory"), ("prod", "postgres"), ("", "postgres")])
def test_default_backend_follows_env_not_db_availability(monkeypatch, env, expected):
    monkeypatch.setattr(settings, "checkpointer_backend", "")
    monkeypatch.setattr(settings, "app_env", env)
    assert cp.resolve_backend() == expected


def test_memory_backend_is_refused_outside_dev(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "checkpointer_backend", "memory")
    with pytest.raises(cp.CheckpointerConfigError, match="memory"):
        cp.assert_safe_checkpointer_config()


def test_unreachable_postgres_fails_startup_instead_of_falling_back(monkeypatch):
    import db.supabase_client as sc

    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "checkpointer_backend", "postgres")
    monkeypatch.setattr(sc, "db_available", lambda: False)
    with pytest.raises(cp.CheckpointerConfigError, match="unreachable"):
        cp.assert_safe_checkpointer_config()
    monkeypatch.setattr(sc, "db_available", lambda: True)
    assert cp.assert_safe_checkpointer_config() == "postgres"


def test_unknown_backend_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "checkpointer_backend", "redis")
    with pytest.raises(cp.CheckpointerConfigError):
        cp.assert_safe_checkpointer_config()


def test_app_startup_runs_the_checkpointer_guard(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "_verify_llm", lambda: False)
    monkeypatch.setattr(main, "db_available", lambda: False)
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "checkpointer_backend", "postgres")  # dev, but explicitly postgres…
    import db.supabase_client as sc
    monkeypatch.setattr(sc, "db_available", lambda: False)               # …and it is unreachable
    with pytest.raises(cp.CheckpointerConfigError):
        with TestClient(main.app):
            pass


def test_postgres_saver_setup_is_refused_without_touching_the_db():
    with pytest.raises(cp.CheckpointerSchemaManagedByMigrations):
        cp.TenantPostgresSaver().setup()


def test_get_checkpointer_is_a_singleton_per_backend():
    first = cp.get_checkpointer()
    assert isinstance(first, cp.TenantMemorySaver)
    assert cp.get_checkpointer() is first
    cp.reset_checkpointer()
    assert cp.get_checkpointer() is not first


# ── memory saver tenant ownership ──────────────────────────────────────
def test_memory_saver_isolates_threads_by_tenant():
    saver = cp.get_checkpointer()
    with use_tenant(A):
        _write_checkpoints(saver, "thread-1")
        assert saver.get_tuple(_cfg("thread-1")) is not None
        assert len(list(saver.list(_cfg("thread-1")))) == 3

    with use_tenant(B):
        assert saver.get_tuple(_cfg("thread-1")) is None            # cannot read
        assert list(saver.list(_cfg("thread-1"))) == []
        assert list(saver.list(None)) == []                         # not even by scanning everything
        with pytest.raises(PermissionError):
            saver.put(_cfg("thread-1"), empty_checkpoint(), {}, {})  # cannot write
        with pytest.raises(PermissionError):
            saver.put_writes({"configurable": {**_cfg("thread-1")["configurable"], "checkpoint_id": "x"}},
                             [("step", 1)], task_id="t")
        saver.delete_thread("thread-1")                             # cannot delete (no-op)

    with use_tenant(A):
        assert saver.get_tuple(_cfg("thread-1")) is not None        # A's thread untouched
        saver.delete_thread("thread-1")
        assert saver.get_tuple(_cfg("thread-1")) is None


# ── lifecycle: erasure deletes the FULL history; export includes it ────
def _seed_candidate_with_threads(saver, email="ada@x.com"):
    job = repository.create_job({"title": "Eng"})
    cand = repository.upsert_candidate({"full_name": "Ada", "email": email})
    apps = [repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]}) for _ in range(2)]
    for app in apps:
        _write_checkpoints(saver, app["id"], n=3)
    return cand, apps


def _thread_is_gone(saver, thread_id: str) -> bool:
    gone = saver.get_tuple(_cfg(thread_id)) is None and list(saver.list(_cfg(thread_id))) == []
    # (reads above re-create empty defaultdict keys; the rows themselves must be gone)
    gone &= all(rows == [] for rows in saver.export_thread(thread_id).values())
    gone &= not any(k[0] == thread_id for k in saver.blobs)
    gone &= not any(k[0] == thread_id for k in saver.writes)
    return gone


def test_candidate_export_includes_thread_history():
    saver = cp.get_checkpointer()
    with use_tenant(A):
        cand, apps = _seed_candidate_with_threads(saver)
        export = repository.export_candidate(cand["id"])
    threads = export["threads"]
    assert len(threads["checkpoints"]) == 6                 # 2 threads × 3 checkpoints
    assert len(threads["checkpoint_writes"]) == 6
    assert {r["thread_id"] for r in threads["checkpoints"]} == {a["id"] for a in apps}
    base64.b64decode(threads["checkpoint_blobs"][0]["blob"])  # JSON-safe encoding of bytea


def test_candidate_erasure_deletes_full_thread_history():
    saver = cp.get_checkpointer()
    with use_tenant(A):
        cand, apps = _seed_candidate_with_threads(saver)
        other_job = repository.create_job({"title": "Other"})
        other = repository.upsert_candidate({"full_name": "Bob", "email": "bob@x.com"})
        other_app = repository.create_application({"job_id": other_job["id"], "candidate_id": other["id"]})
        _write_checkpoints(saver, other_app["id"], n=1)

        repository.erase_candidate(cand["id"])

        assert all(_thread_is_gone(saver, app["id"]) for app in apps)   # every version
        assert saver.get_tuple(_cfg(other_app["id"])) is not None        # unrelated thread kept


def test_erasure_removes_subgraph_namespaces_too():
    """Erasure deletes by thread_id across ALL checkpoint_ns values: a
    checkpoint written under a subgraph namespace goes with the root one."""
    saver = cp.get_checkpointer()
    with use_tenant(A):
        job = repository.create_job({"title": "Eng"})
        cand = repository.upsert_candidate({"full_name": "Ada", "email": "ada@x.com"})
        app = repository.create_application({"job_id": job["id"], "candidate_id": cand["id"]})
        _write_checkpoints(saver, app["id"], n=2)                        # root namespace
        _write_checkpoints(saver, app["id"], n=2, ns="score:child-task")  # subgraph namespace
        assert saver.get_tuple(_cfg(app["id"], "score:child-task")) is not None
        assert len(saver.export_thread(app["id"])["checkpoints"]) == 4

        repository.erase_candidate(cand["id"])

        assert saver.get_tuple(_cfg(app["id"], "score:child-task")) is None
        assert saver.get_tuple(_cfg(app["id"], "")) is None
        assert _thread_is_gone(saver, app["id"])


def test_retention_erasure_also_removes_threads():
    from services import retention

    saver = cp.get_checkpointer()
    with use_tenant(A):
        repository.update_organization(A, {"retention_days": 30})
        cand, apps = _seed_candidate_with_threads(saver)
        repository._candidates[cand["id"]]["created_at"] = "2000-01-01T00:00:00+00:00"
        assert retention.enforce(A)["candidates_erased"] == 1
        assert all(_thread_is_gone(saver, a["id"]) for a in apps)


def test_tenant_purge_removes_only_that_tenants_threads():
    saver = cp.get_checkpointer()
    with use_tenant(A):
        _, apps_a = _seed_candidate_with_threads(saver, "a@x.com")
    with use_tenant(B):
        _, apps_b = _seed_candidate_with_threads(saver, "b@x.com")
        export_b = repository.export_tenant(B)
    assert len(export_b["threads"]["checkpoints"]) == 6

    repository.delete_tenant(A)

    with use_tenant(A):
        assert all(_thread_is_gone(saver, a["id"]) for a in apps_a)
    with use_tenant(B):
        assert all(saver.get_tuple(_cfg(a["id"])) is not None for a in apps_b)
