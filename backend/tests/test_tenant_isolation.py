"""Tenant isolation in the in-memory repository path + export/delete scoping.

The Postgres RLS proof lives in scripts/verify_rls.py (run against a real DB);
these cover the in-memory fallback the app uses when Postgres is unavailable.
"""

from __future__ import annotations

import pytest

from db import repository
from services.tenant_context import use_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

_STORES = (
    "_jobs", "_candidates", "_applications", "_emails", "_generated_jds", "_orgs",
)


@pytest.fixture(autouse=True)
def _force_inmemory_and_clear(monkeypatch):
    monkeypatch.setattr(repository, "db_available", lambda: False)
    for name in _STORES:
        getattr(repository, name).clear()
    yield
    for name in _STORES:
        getattr(repository, name).clear()


def test_jobs_isolated_by_tenant():
    with use_tenant(A):
        ja = repository.create_job({"title": "A job"})
    with use_tenant(B):
        jb = repository.create_job({"title": "B job"})
        # B sees only its own job, and cannot read A's by id.
        assert [j["title"] for j in repository.list_jobs()] == ["B job"]
        assert repository.get_job(ja["id"]) is None
    with use_tenant(A):
        assert [j["title"] for j in repository.list_jobs()] == ["A job"]
        assert repository.get_job(jb["id"]) is None


def test_applications_and_candidates_isolated():
    with use_tenant(A):
        j = repository.create_job({"title": "A"})
        c = repository.upsert_candidate({"full_name": "Ada", "email": "ada@a.com"})
        repository.create_application({"job_id": j["id"], "candidate_id": c["id"]})

    with use_tenant(B):
        # B's view of A's job id is empty, and global aggregates are tenant-scoped.
        assert repository.list_applications_for_job(j["id"]) == []
        assert repository.count_applications_by_source() == {}
        assert repository.get_candidate(c["id"]) is None

    with use_tenant(A):
        assert len(repository.list_applications_for_job(j["id"])) == 1
        assert repository.get_candidate(c["id"]) is not None


def test_same_linkedin_url_allowed_across_tenants():
    """Per-tenant identity: two tenants may each hold the same candidate URL."""
    url = "https://linkedin.com/in/shared"
    with use_tenant(A):
        ca = repository.upsert_candidate({"full_name": "X", "linkedin_url": url})
    with use_tenant(B):
        cb = repository.upsert_candidate({"full_name": "Y", "linkedin_url": url})
    assert ca["id"] != cb["id"]


def test_export_and_delete_are_tenant_scoped():
    with use_tenant(A):
        repository.create_job({"title": "A"})
        repository.upsert_candidate({"full_name": "Ada", "email": "ada@a.com"})
    with use_tenant(B):
        repository.create_job({"title": "B"})

    export_a = repository.export_tenant(A)
    assert [j["title"] for j in export_a["jobs"]] == ["A"]
    assert len(export_a["candidates"]) == 1

    deleted = repository.delete_tenant(A)
    assert deleted["jobs"] == 1 and deleted["candidates"] == 1

    with use_tenant(A):
        assert repository.list_jobs() == []
    with use_tenant(B):
        assert len(repository.list_jobs()) == 1  # B untouched
