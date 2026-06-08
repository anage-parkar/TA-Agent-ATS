-- Link a Google/MS Form to a specific job, so each job syncs only its own
-- form's responses (instead of a single global form affecting every job).

alter table jobs add column if not exists form_id text;
