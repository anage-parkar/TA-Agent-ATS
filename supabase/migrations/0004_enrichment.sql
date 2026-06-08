-- Store LinkedIn-scraped enrichment for a candidate (about, experiences,
-- educations, skills) and when it was last refreshed.

alter table candidates add column if not exists enrichment jsonb;
alter table candidates add column if not exists enriched_at timestamptz;
