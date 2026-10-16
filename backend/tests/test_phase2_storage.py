"""Phase 2 / PR 1 — checkpoint storage: tenant-scoped savers, explicit backend
selection, lifecycle parity (export / erasure), the pinned-package-vs-vendored
DDL check, and the structural no-PII guarantee on graph state.

No graph exists yet; nothing here exercises a request path except the
lifecycle hooks the repository already exposes.
"""

from __future__ import annotations

import base64
import importlib.metadata
import pathlib
import re
import typing
from datetime import datetime, timezone
from enum import Enum

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from pydantic import BaseModel

from db import repository
from orchestrator import checkpointer as cp
from orchestrator.state import IdMarker, ThreadState
from services import redaction
from services.config import settings
from services.tenant_context import use_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
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


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _write_checkpoints(saver, thread_id: str, n: int = 3) -> None:
    """Write n checkpoints (and one pending write each) to a thread."""
    for i in range(n):
        ck = empty_checkpoint()
        ck["channel_values"] = {"step": i}
        saved = saver.put(_cfg(thread_id), ck, {"source": "input", "step": i, "parents": {}}, {"step": str(i + 1)})
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
        ours = re.search(rf"create table if not exists {table} \((.*?)\n\);", text, re.S).group(1)
        for col in columns:
            assert re.search(rf"^\s*{col}\s", ours, re.M), f"{table}.{col} missing from 0011"
        assert re.search(r"^\s*tenant_id\s", ours, re.M)
    assert "task_path" in text  # package migration 9
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        assert f"create policy tenant_isolation on %i" in text and f"'{table}'" in text
    assert "revoke all on checkpoint_migrations from app_user" in text


# ── graph state holds references, not PII ─────────────────────────────
_SCALARS = (bool, int, float, datetime)


def _id_marked(annotation, field) -> bool:
    if any(isinstance(m, IdMarker) for m in getattr(field, "metadata", [])):
        return True
    if typing.get_origin(annotation) is typing.Annotated:
        return any(isinstance(m, IdMarker) for m in typing.get_args(annotation)[1:])
    return False


def _check(annotation, path: str, field=None) -> list[str]:
    """Return the paths of every field that is not an id, enum, number,
    timestamp, boolean, or a nested model/list of those."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        inner = typing.get_args(annotation)[0]
        if inner is str and _id_marked(annotation, field):
            return []
        return _check(inner, path, field)
    if origin in (typing.Union, getattr(typing, "UnionType", None)) or str(origin) == "<class 'types.UnionType'>":
        return [p for arg in typing.get_args(annotation) if arg is not type(None)
                for p in _check(arg, path, field)]
    if origin is list:
        return _check(typing.get_args(annotation)[0], path + "[]", field)
    if annotation is str:
        return [] if _id_marked(annotation, field) else [path]
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


def test_state_schema_rejects_free_text_ids_and_unknown_fields():
    base = dict(application_id="app-1", candidate_id="cand-1", job_id="job-1", tenant_id=A,
                graph_version=1, started_by="system:scheduler", status="running", orchestrated=False)
    ThreadState(**base)
    with pytest.raises(ValueError):
        ThreadState(**{**base, "started_by": "Ada Lovelace"})      # whitespace: prose, not an id
    with pytest.raises(ValueError):
        ThreadState(**{**base, "note": "call candidate tomorrow"})  # extra field forbidden


def test_serialised_state_trips_no_pii_detector():
    """Second layer: a fully populated state, serialised, contains no email,
    affinity marker, or whitespace-bearing string (the shape prose takes)."""
    state = ThreadState(
        application_id="4b1c0d4e-1111-4e8b-9b3a-000000000001", candidate_id="4b1c0d4e-2222-4e8b-9b3a-000000000002",
        job_id="4b1c0d4e-3333-4e8b-9b3a-000000000003", tenant_id=A, graph_version=1,
        started_by="00000000-0000-0000-0000-000000000002", status="waiting_human", orchestrated=True,
        last_event_id="evt-7", score_summary={"overall": 72.5, "passed_knockouts": True, "below_threshold": False},
        decisions=[{"gate": "review_gate", "outcome": "proceed", "by": "system:orchestrator",
                    "at": datetime.now(timezone.utc), "event_id": "evt-6"}],
        outreach={"email_id": "em-1", "idempotency_key": "app:send_gate:evt-6", "send_status": "pending"},
    )
    payload = state.model_dump_json()
    assert not redaction._EMAIL_RE.search(payload)
    assert not redaction._AFFINITY_RE.search(payload)
    for value in re.findall(r'"([^"]*)"', payload):
        assert " " not in value, f"prose-like value in checkpoint payload: {value!r}"


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
            saver.put_writes(_cfg("thread-1") | {"configurable": {**_cfg("thread-1")["configurable"], "checkpoint_id": "x"}},
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


def test_candidate_export_includes_thread_history():
    saver = cp.get_checkpointer()
    with use_tenant(A):
        cand, apps = _seed_candidate_with_threads(saver)
        export = repository.export_candidate(cand["id"])
    threads = export["threads"]
    assert len(threads["checkpoints"]) == 6                 # 2 threads × 3 checkpoints
    assert len(threads["checkpoint_writes"]) == 6
    assert {r["thread_id"] for r in threads["checkpoints"]} == {a["id"] for a in apps}
    blob = threads["checkpoint_blobs"][0]["blob"]
    base64.b64decode(blob)                                   # JSON-safe encoding of bytea


def test_candidate_erasure_deletes_full_thread_history():
    saver = cp.get_checkpointer()
    with use_tenant(A):
        cand, apps = _seed_candidate_with_threads(saver)
        other_job = repository.create_job({"title": "Other"})
        other = repository.upsert_candidate({"full_name": "Bob", "email": "bob@x.com"})
        other_app = repository.create_application({"job_id": other_job["id"], "candidate_id": other["id"]})
        _write_checkpoints(saver, other_app["id"], n=1)

        repository.erase_candidate(cand["id"])

        for app in apps:
            assert saver.get_tuple(_cfg(app["id"])) is None
            assert list(saver.list(_cfg(app["id"]))) == []      # every version, not just the latest
            # (reads above re-create empty defaultdict keys; the rows themselves are gone)
            assert all(rows == [] for rows in saver.export_thread(app["id"]).values())
            assert not any(k[0] == app["id"] for k in saver.blobs)
            assert not any(k[0] == app["id"] for k in saver.writes)
        assert saver.get_tuple(_cfg(other_app["id"])) is not None  # unrelated thread kept


def test_retention_erasure_also_removes_threads():
    from services import retention

    saver = cp.get_checkpointer()
    with use_tenant(A):
        repository.update_organization(A, {"retention_days": 30})
        cand, apps = _seed_candidate_with_threads(saver)
        repository._candidates[cand["id"]]["created_at"] = "2000-01-01T00:00:00+00:00"
        assert retention.enforce(A)["candidates_erased"] == 1
        assert all(saver.get_tuple(_cfg(a["id"])) is None for a in apps)


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
        assert all(saver.get_tuple(_cfg(a["id"])) is None for a in apps_a)
    with use_tenant(B):
        assert all(saver.get_tuple(_cfg(a["id"])) is not None for a in apps_b)
