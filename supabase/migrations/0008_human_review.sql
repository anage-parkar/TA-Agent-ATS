-- 0008_human_review.sql — Workstream C: human-in-the-loop rejection.
--
-- Candidates can request human review of an automated outcome (transparency,
-- ties to Workstream F). Recruiters see flagged applications in the review queue.
-- (The "rejected transition requires a human actor" rule is enforced in the
-- application layer at the single mutation point — db/repository.update_application.)

alter table applications add column if not exists human_review_requested boolean default false;

create index if not exists applications_human_review_idx
  on applications(human_review_requested)
  where human_review_requested;
