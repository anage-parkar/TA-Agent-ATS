"""Prove tenant isolation via Postgres RLS against a real database.

Applies all migrations, then connects as a NON-superuser, NON-BYPASSRLS login
role and `SET ROLE`s to `app_user` — the NOLOGIN group role migration 0006
creates, which is the role the application must run as in production. Every
table privilege the checks rely on therefore comes from the migration's own
grants, so a passing run proves the production role configuration, not an
ad-hoc test role. (Superusers/owners bypass RLS, so testing as `postgres`
would prove nothing.) Asserts that, with only the `app.tenant_id` GUC set and
NO WHERE clause, queries return zero cross-tenant rows.

Usage (local, throwaway Postgres with pgvector):
    RLS_TEST_DATABASE_URL=postgresql://postgres:localpassword@localhost:5433/ta_agent \
        python scripts/verify_rls.py

The login role defaults to rls_tester/testpw and is created if missing; set
RLS_TEST_ROLE / RLS_TEST_ROLE_PASSWORD to use one created up front (CI does
this in .github/workflows/rls-verify.yml).
"""

from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from psycopg import sql

ADMIN_URL = os.environ.get(
    "RLS_TEST_DATABASE_URL",
    "postgresql://postgres:localpassword@localhost:5433/ta_agent",
)
MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"

ORG_A = "00000000-0000-0000-0000-000000000001"  # seeded default org
ORG_B = "00000000-0000-0000-0000-0000000000bb"

APP_ROLE = "app_user"  # NOLOGIN group role from migration 0006 — the app's production role
TESTER_ROLE = os.environ.get("RLS_TEST_ROLE", "rls_tester")
TESTER_PASSWORD = os.environ.get("RLS_TEST_ROLE_PASSWORD", "testpw")


def _tester_url() -> str:
    # same host/db as admin, but the non-privileged login role
    u = psycopg.conninfo.conninfo_to_dict(ADMIN_URL)
    return psycopg.conninfo.make_conninfo(
        user=TESTER_ROLE, password=TESTER_PASSWORD,
        host=u.get("host"), port=u.get("port"), dbname=u.get("dbname"),
    )


def apply_migrations(conn) -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        conn.execute(sql)
        print(f"  applied {path.name}")


def setup_roles_and_orgs(conn) -> None:
    # The login role may already exist (CI creates it up front); otherwise
    # create it. Either way it is a plain, non-privileged login whose ONLY
    # table privileges arrive through membership of the migration's app role.
    exists = conn.execute(
        "select 1 from pg_roles where rolname = %s", (TESTER_ROLE,)
    ).fetchone()
    if not exists:
        conn.execute(
            sql.SQL("create role {} login password {} nosuperuser nobypassrls").format(
                sql.Identifier(TESTER_ROLE), sql.Literal(TESTER_PASSWORD)
            )
        )
    conn.execute(sql.SQL("alter role {} nosuperuser nobypassrls").format(sql.Identifier(TESTER_ROLE)))
    conn.execute(sql.SQL("grant {} to {}").format(sql.Identifier(APP_ROLE), sql.Identifier(TESTER_ROLE)))
    # second tenant org (as owner — bypasses RLS for setup)
    conn.execute(
        "insert into organizations (id, name, slug) values (%s, 'Tenant B', 'tenant-b') "
        "on conflict (id) do nothing",
        (ORG_B,),
    )


def check(label: str, got, expected) -> bool:
    ok = got == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")
    return ok


def run_isolation_checks() -> bool:
    results = []
    with psycopg.connect(_tester_url(), autocommit=True) as conn:
        # Act as the production app role (NOLOGIN → reachable only via SET ROLE)
        # and prove it is neither a superuser nor BYPASSRLS before trusting any
        # of the checks below.
        conn.execute(sql.SQL("set role {}").format(sql.Identifier(APP_ROLE)))
        privs = conn.execute(
            "select current_user, rolsuper, rolbypassrls from pg_roles where rolname = current_user"
        ).fetchone()
        results.append(check("effective role is app_user, non-superuser, non-BYPASSRLS",
                             privs, (APP_ROLE, False, False)))

        # Insert one job per tenant (tenant_id auto-fills from the GUC default).
        conn.execute("select set_config('app.tenant_id', %s, false)", (ORG_A,))
        a_id = conn.execute(
            "insert into jobs (title) values ('A job') returning id"
        ).fetchone()[0]

        conn.execute("select set_config('app.tenant_id', %s, false)", (ORG_B,))
        conn.execute("insert into jobs (title) values ('B job')")

        # As tenant B, a query with NO tenant filter sees only B's rows.
        titles_b = [r[0] for r in conn.execute("select title from jobs").fetchall()]
        results.append(check("tenant B sees only its own jobs (no WHERE)", titles_b, ["B job"]))

        # B cannot read A's job by id.
        b_view_of_a = conn.execute("select count(*) from jobs where id = %s", (a_id,)).fetchone()[0]
        results.append(check("tenant B cannot read A's job by id", b_view_of_a, 0))

        # Switch to A: sees only A.
        conn.execute("select set_config('app.tenant_id', %s, false)", (ORG_A,))
        titles_a = [r[0] for r in conn.execute("select title from jobs").fetchall()]
        results.append(check("tenant A sees only its own jobs", titles_a, ["A job"]))

        # Fail-closed: with no tenant bound, zero rows are visible.
        conn.execute("select set_config('app.tenant_id', '', false)")
        unbound = conn.execute("select count(*) from jobs").fetchone()[0]
        results.append(check("unbound connection sees zero rows (fail-closed)", unbound, 0))

    return all(results)


def main() -> int:
    print(f"Applying migrations from {MIGRATIONS} to {ADMIN_URL} ...")
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        apply_migrations(conn)
        setup_roles_and_orgs(conn)
    print(f"Running isolation checks as {TESTER_ROLE!r} with SET ROLE {APP_ROLE!r} ...")
    ok = run_isolation_checks()
    print("\nRESULT:", "ALL CHECKS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
