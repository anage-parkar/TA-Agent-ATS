-- 0007_data_model.sql — Workstream B: candidate-as-a-person, event backbone,
-- audit log, explainable AI decisions, and compliance tables.
--
-- Extends the existing person/application model rather than recreating it
-- (candidates is already the person, applications the candidate×job join).
-- Every new table carries tenant_id + FORCE RLS, consistent with 0006.

-- ── Candidate-as-a-person ──────────────────────────────────────────────
alter table candidates add column if not exists identity_keys jsonb;   -- {email_hash,phone_hash,name_hash} for dedup/identity resolution
alter table candidates add column if not exists parsed_profile jsonb;
alter table candidates add column if not exists tags text[];
create index if not exists candidates_identity_keys_idx on candidates using gin (identity_keys);

-- ── Jobs: structured requirements + status/approval (rubric weights land
--    fully in Workstream D; columns added now) ──────────────────────────
alter table jobs add column if not exists status text default 'open';
alter table jobs add column if not exists approval_state text default 'approved';
alter table jobs add column if not exists requirements jsonb;
alter table jobs add column if not exists weights jsonb;
alter table jobs add column if not exists threshold numeric;

-- ── Applications: when the person entered this req ──────────────────────
alter table applications add column if not exists applied_at timestamptz default now();

-- ── Per-job ordered pipeline stages + transition rules ─────────────────
create table if not exists pipeline_stages (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid
    references organizations(id) on delete cascade,
  job_id uuid not null references jobs(id) on delete cascade,
  name text not null,
  position int not null default 0,
  transitions jsonb,                 -- allowed next stages / rules
  created_at timestamptz default now(),
  unique (job_id, name)
);
create index if not exists pipeline_stages_job_idx on pipeline_stages(job_id);

-- ── Append-only activity / audit timeline (incl. AI actions) ───────────
create table if not exists activity_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid
    references organizations(id) on delete cascade,
  actor_type text not null,          -- 'human' | 'ai' | 'system'
  actor_id text,                     -- user id, agent name, or null
  action text not null,              -- e.g. application.created, application.decided
  entity_type text,                  -- application | candidate | job | ...
  entity_id text,
  before jsonb,
  after jsonb,
  metadata jsonb,
  created_at timestamptz default now()
);
create index if not exists activity_events_entity_idx on activity_events(entity_type, entity_id);
create index if not exists activity_events_created_idx on activity_events(created_at);

-- ── Explainable AI decisions (one row per AI output) ───────────────────
create table if not exists ai_decisions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid
    references organizations(id) on delete cascade,
  application_id uuid references applications(id) on delete cascade,
  candidate_id uuid references candidates(id) on delete set null,
  job_id uuid references jobs(id) on delete set null,
  kind text not null,                -- 'ats_score' | 'jd_parse' | 'reply_intent' | ...
  model_version text,
  prompt_name text,
  prompt_version int,
  prompt_hash text,
  input_hash text,
  scores jsonb,                      -- per-dimension scores
  evidence jsonb,                    -- citations / reasoning
  decision text,
  reviewer_id text,                  -- human who reviewed (null until reviewed)
  reviewed_at timestamptz,
  created_at timestamptz default now()
);
create index if not exists ai_decisions_application_idx on ai_decisions(application_id);
create index if not exists ai_decisions_kind_idx on ai_decisions(kind);

-- ── Structured human interview feedback ────────────────────────────────
create table if not exists scorecards (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid
    references organizations(id) on delete cascade,
  application_id uuid references applications(id) on delete cascade,
  interviewer_id text,
  rubric jsonb,
  ratings jsonb,
  recommendation text,               -- strong_yes | yes | no | strong_no
  notes text,
  created_at timestamptz default now()
);
create index if not exists scorecards_application_idx on scorecards(application_id);

-- ── Consent records (lawful basis per region/scope) ────────────────────
create table if not exists consent_records (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid
    references organizations(id) on delete cascade,
  candidate_id uuid references candidates(id) on delete cascade,
  lawful_basis text,                 -- consent | legitimate_interest | contract | ...
  scope text,
  region text,
  granted_at timestamptz,
  expires_at timestamptz,
  created_at timestamptz default now()
);
create index if not exists consent_candidate_idx on consent_records(candidate_id);

-- ── EEO / voluntary diversity data — SEGREGATED ────────────────────────
-- Deliberately isolated: nothing in the scoring path reads this table, and it
-- is never joined to candidates/applications for scoring (Workstream F reports
-- on it in aggregate only).
create table if not exists eeo_records (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid
    references organizations(id) on delete cascade,
  candidate_id uuid references candidates(id) on delete cascade,
  data jsonb,
  created_at timestamptz default now()
);

-- ── Resumes: raw ref + parsed + redacted (scoring sees redacted only) ──
create table if not exists resumes (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null default nullif(current_setting('app.tenant_id', true), '')::uuid
    references organizations(id) on delete cascade,
  candidate_id uuid references candidates(id) on delete cascade,
  file_url text,
  parsed_profile jsonb,
  redacted_profile jsonb,            -- the version the scorer is allowed to see (Workstream D)
  created_at timestamptz default now()
);
create index if not exists resumes_candidate_idx on resumes(candidate_id);

-- ── RLS (FORCE) on every new tenant table ──────────────────────────────
do $$
declare t text;
begin
  foreach t in array array['pipeline_stages','activity_events','ai_decisions','scorecards','consent_records','eeo_records','resumes']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists tenant_isolation on %I', t);
    execute format(
      $p$create policy tenant_isolation on %I
           using (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
           with check (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)$p$, t);
    execute format('create index if not exists %I on %I(tenant_id)', t || '_tenant_idx', t);
  end loop;
end $$;

-- grants for the non-bypass app role (new tables)
grant select, insert, update, delete on all tables in schema public to app_user;
grant usage, select on all sequences in schema public to app_user;
