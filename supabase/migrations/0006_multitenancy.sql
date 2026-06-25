-- 0006_multitenancy.sql — Workstream A: multi-tenancy, RLS, RBAC tables.
--
-- Adds the tenant model (organizations/users/memberships), tags every domain
-- table with tenant_id, and enforces isolation with Postgres Row-Level Security
-- keyed on the `app.tenant_id` GUC (set per connection by the app — see
-- db/supabase_client.tenant_connection). RLS is FORCEd so even the table owner
-- is subject to it; the app must connect as a NON-superuser, NON-BYPASSRLS role
-- (app_user, created below) for RLS to take effect.

create extension if not exists pgcrypto;   -- gen_random_uuid()

-- ── Control-plane tables (no tenant RLS — these define tenancy) ─────────
create table if not exists organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique,
  region text default 'global',          -- data-residency hint (Workstream F)
  created_at timestamptz default now()
);

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  full_name text,
  created_at timestamptz default now()
);

create table if not exists memberships (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  user_id uuid not null references users(id) on delete cascade,
  role text not null check (role in
    ('admin','recruiter','hiring_manager','coordinator','interviewer')),
  created_at timestamptz default now(),
  unique (org_id, user_id)
);
create index if not exists memberships_org_idx on memberships(org_id);
create index if not exists memberships_user_idx on memberships(user_id);

-- ── Seed the default org + admin (idempotent) ──────────────────────────
insert into organizations (id, name, slug)
  values ('00000000-0000-0000-0000-000000000001', 'Default Organization', 'default')
  on conflict (id) do nothing;
insert into users (id, email, full_name)
  values ('00000000-0000-0000-0000-000000000002', 'admin@local', 'Default Admin')
  on conflict (id) do nothing;
insert into memberships (org_id, user_id, role)
  values ('00000000-0000-0000-0000-000000000001',
          '00000000-0000-0000-0000-000000000002', 'admin')
  on conflict (org_id, user_id) do nothing;

-- ── generated_jds: ensure it exists before we add tenant_id ────────────
create table if not exists generated_jds (
  id uuid primary key default gen_random_uuid(),
  business_unit text,
  role text,
  designation text,
  years_of_experience int,
  skills jsonb,
  content jsonb,
  pdf_base64 text,
  pdf_url text,
  created_at timestamptz default now()
);

-- ── Job-scoped membership (hiring team — a hiring manager sees only theirs) ──
create table if not exists job_members (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid
    references organizations(id) on delete cascade,
  job_id uuid not null references jobs(id) on delete cascade,
  user_id uuid not null references users(id) on delete cascade,
  created_at timestamptz default now(),
  unique (job_id, user_id)
);
create index if not exists job_members_job_idx on job_members(job_id);
create index if not exists job_members_tenant_idx on job_members(tenant_id);

-- ── Add tenant_id to every domain table, backfill, enforce, index ──────
do $$
declare t text;
begin
  foreach t in array array['jobs','candidates','applications','emails','interviews','generated_jds']
  loop
    execute format(
      'alter table %I add column if not exists tenant_id uuid references organizations(id) on delete cascade', t);
    execute format(
      'update %I set tenant_id = %L where tenant_id is null', t,
      '00000000-0000-0000-0000-000000000001');
    execute format('alter table %I alter column tenant_id set not null', t);
    execute format(
      $d$alter table %I alter column tenant_id set default nullif(current_setting('app.tenant_id', true), '')::uuid$d$, t);
    execute format('create index if not exists %I on %I(tenant_id)', t || '_tenant_idx', t);
  end loop;
end $$;

-- ── Per-tenant uniqueness (global keys would block two tenants holding the
--    same candidate URL / job source URL) ──────────────────────────────
alter table jobs drop constraint if exists jobs_source_url_key;
alter table jobs drop constraint if exists jobs_tenant_source_url_key;
alter table jobs add constraint jobs_tenant_source_url_key unique (tenant_id, source_url);

alter table candidates drop constraint if exists candidates_linkedin_url_key;
alter table candidates drop constraint if exists candidates_tenant_linkedin_key;
alter table candidates add constraint candidates_tenant_linkedin_key unique (tenant_id, linkedin_url);

-- ── Row-Level Security — tenant isolation backstop ─────────────────────
-- FORCE so the table owner is also subject. Policy reads the per-connection
-- GUC; `current_setting('app.tenant_id', true)` returns NULL when unset, so an
-- unbound connection sees zero rows (fail-closed).
do $$
declare t text;
begin
  foreach t in array array['jobs','candidates','applications','emails','interviews','generated_jds','job_members']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists tenant_isolation on %I', t);
    execute format(
      $p$create policy tenant_isolation on %I
           using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
           with check (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)$p$, t);
  end loop;
end $$;

-- memberships are isolated by org_id (a tenant only sees its own members).
alter table memberships enable row level security;
alter table memberships force row level security;
drop policy if exists tenant_isolation on memberships;
create policy tenant_isolation on memberships
  using (org_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
  with check (org_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

-- ── Application role that does NOT bypass RLS ──────────────────────────
-- The app MUST connect as this (or another NOSUPERUSER/NOBYPASSRLS) role for
-- RLS to apply; superusers and BYPASSRLS roles ignore policies entirely.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_user') then
    create role app_user nologin noinherit;
  end if;
end $$;
grant usage on schema public to app_user;
grant select, insert, update, delete on all tables in schema public to app_user;
grant usage, select on all sequences in schema public to app_user;
alter default privileges in schema public
  grant select, insert, update, delete on tables to app_user;
alter default privileges in schema public
  grant usage, select on sequences to app_user;
