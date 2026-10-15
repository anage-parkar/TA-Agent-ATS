-- 0011_checkpointer.sql — Phase 2 / PR 1: LangGraph checkpoint storage, tenant-scoped.
--
-- The three LangGraph tables are VENDORED here from the pinned package so the
-- schema is owned by migrations, never by the package's PostgresSaver.setup()
-- (which the application role cannot run: it has no CREATE on the schema and no
-- privileges on checkpoint_migrations). A CI test compares the two markers
-- below with the installed package; bump the pin => write a new migration.
--
-- @langgraph-checkpoint-postgres: 3.2.0
-- @langgraph-migrations: 10
--
-- Differences from the package DDL, all additive:
--   * tenant_id on every table (defaulted from the app.tenant_id GUC like every
--     domain table), FORCE row-level security with the same tenant policy.
--   * indexes created without CONCURRENTLY (fresh tables; migrations run inside
--     a transaction, where CONCURRENTLY is not allowed).
--   * checkpoint_migrations is pre-filled with v = 0..9 so the package believes
--     the schema is current; the application role is explicitly denied on it.

create table if not exists checkpoint_migrations (
  v integer primary key
);

create table if not exists checkpoints (
  thread_id            text not null,
  checkpoint_ns        text not null default '',
  checkpoint_id        text not null,
  parent_checkpoint_id text,
  type                 text,
  checkpoint           jsonb not null,
  metadata             jsonb not null default '{}',
  tenant_id            uuid not null
                       default nullif(current_setting('app.tenant_id', true), '')::uuid
                       references organizations(id) on delete cascade,
  primary key (thread_id, checkpoint_ns, checkpoint_id)
);

create table if not exists checkpoint_blobs (
  thread_id     text not null,
  checkpoint_ns text not null default '',
  channel       text not null,
  version       text not null,
  type          text not null,
  blob          bytea,                      -- nullable (package migration 4)
  tenant_id     uuid not null
                default nullif(current_setting('app.tenant_id', true), '')::uuid
                references organizations(id) on delete cascade,
  primary key (thread_id, checkpoint_ns, channel, version)
);

create table if not exists checkpoint_writes (
  thread_id     text not null,
  checkpoint_ns text not null default '',
  checkpoint_id text not null,
  task_id       text not null,
  idx           integer not null,
  channel       text not null,
  type          text,
  blob          bytea not null,
  task_path     text not null default '',   -- package migration 9
  tenant_id     uuid not null
                default nullif(current_setting('app.tenant_id', true), '')::uuid
                references organizations(id) on delete cascade,
  primary key (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

create index if not exists checkpoints_thread_id_idx       on checkpoints(thread_id);
create index if not exists checkpoint_blobs_thread_id_idx  on checkpoint_blobs(thread_id);
create index if not exists checkpoint_writes_thread_id_idx on checkpoint_writes(thread_id);
create index if not exists checkpoints_tenant_idx          on checkpoints(tenant_id);
create index if not exists checkpoint_blobs_tenant_idx     on checkpoint_blobs(tenant_id);
create index if not exists checkpoint_writes_tenant_idx    on checkpoint_writes(tenant_id);

-- Mark every package migration as applied (0..9 for 3.2.0).
insert into checkpoint_migrations (v)
  select generate_series(0, 9)
  on conflict (v) do nothing;

-- ── Tenant isolation: identical policy to the domain tables (0006) ────────
do $$
declare t text;
begin
  foreach t in array array['checkpoints', 'checkpoint_blobs', 'checkpoint_writes']
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

-- ── Privileges ────────────────────────────────────────────────────────────
-- The app role gets DML on the checkpoint tables (0006's default privileges
-- would grant this anyway; stated explicitly) and NOTHING on the package's
-- migration bookkeeping table, so PostgresSaver.setup() cannot run as app_user.
grant select, insert, update, delete on checkpoints, checkpoint_blobs, checkpoint_writes to app_user;
revoke all on checkpoint_migrations from app_user;
