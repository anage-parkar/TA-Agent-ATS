-- 0010_engagement.sql — Workstream G: opt-out (GDPR) + engagement hardening.

alter table candidates add column if not exists opted_out boolean default false;
alter table candidates add column if not exists opted_out_at timestamptz;

create index if not exists candidates_opted_out_idx
  on candidates(opted_out) where opted_out;
