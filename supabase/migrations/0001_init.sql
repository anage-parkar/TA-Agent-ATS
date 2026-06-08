-- TA Agent — initial schema
-- Applied automatically on first boot of a fresh Postgres volume
-- (mounted at /docker-entrypoint-initdb.d), or via `make migrate`.

-- ── Extensions ────────────────────────────────────────────────────────
create extension if not exists "pgcrypto";   -- gen_random_uuid()
create extension if not exists vector;        -- pgvector

-- ── Jobs ──────────────────────────────────────────────────────────────
create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  source_url text unique,
  raw_html text,
  skills text[],
  skills_nice_to_have text[],
  seniority text,
  location text,
  salary_range jsonb,
  responsibilities text[],
  tech_stack text[],
  parsed_at timestamptz,
  created_at timestamptz default now()
);

-- ── Candidates ────────────────────────────────────────────────────────
create table if not exists candidates (
  id uuid primary key default gen_random_uuid(),
  full_name text,
  linkedin_url text unique,
  email text,
  headline text,
  skills text[],
  experience_years int,
  location text,
  raw_profile jsonb,
  embedding vector(1536),
  created_at timestamptz default now()
);

-- IVFFlat index for cosine similarity search (built lazily; fine for local dev)
create index if not exists candidates_embedding_idx
  on candidates using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- ── Applications (candidate × job) ────────────────────────────────────
create table if not exists applications (
  id uuid primary key default gen_random_uuid(),
  job_id uuid references jobs(id) on delete cascade,
  candidate_id uuid references candidates(id) on delete cascade,
  ats_score numeric(5,2),
  ats_breakdown jsonb,
  status text default 'sourced',
  recruiter_decision text,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (job_id, candidate_id)
);

create index if not exists applications_job_idx on applications(job_id);
create index if not exists applications_status_idx on applications(status);

-- ── Emails ────────────────────────────────────────────────────────────
create table if not exists emails (
  id uuid primary key default gen_random_uuid(),
  application_id uuid references applications(id) on delete cascade,
  direction text,                 -- 'outbound' | 'inbound'
  subject text,
  body text,
  sent_at timestamptz,
  opened_at timestamptz,
  replied_at timestamptz,
  intent text,                    -- accepted | declined | question | no_reply
  raw_reply text,
  thread_id text,
  created_at timestamptz default now()
);

create index if not exists emails_application_idx on emails(application_id);
create index if not exists emails_thread_idx on emails(thread_id);

-- ── Interviews ────────────────────────────────────────────────────────
create table if not exists interviews (
  id uuid primary key default gen_random_uuid(),
  application_id uuid references applications(id) on delete cascade,
  stage text,                     -- Technical | HR | Culture | Final
  scheduled_at timestamptz,
  duration_minutes int default 60,
  format text,                    -- Video | Phone | Onsite
  interviewer_email text,
  calendar_event_id text,
  confirmation_status text default 'pending',
  created_at timestamptz default now()
);

create index if not exists interviews_application_idx on interviews(application_id);

-- ── updated_at trigger for applications ───────────────────────────────
create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists applications_updated_at on applications;
create trigger applications_updated_at
  before update on applications
  for each row execute function set_updated_at();
