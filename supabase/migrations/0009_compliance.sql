-- 0009_compliance.sql — Workstream F: compliance & data-lifecycle config.
--
-- Per-tenant compliance settings live on the organization (control-plane table,
-- no RLS). region already exists (0006) for data-residency routing.

alter table organizations add column if not exists retention_days int;          -- null = no auto-deletion
alter table organizations add column if not exists ai_disclosure text;          -- candidate-facing AI-use notice (null = platform default)
alter table organizations add column if not exists privacy_notice_url text;
