-- Applicant ingestion: capture contact info + resume, and track where each
-- application came from (offsite form, LinkedIn Apply Connect, sourcing, etc.).

alter table candidates add column if not exists phone text;
alter table candidates add column if not exists resume_url text;

alter table applications add column if not exists source text default 'manual';
