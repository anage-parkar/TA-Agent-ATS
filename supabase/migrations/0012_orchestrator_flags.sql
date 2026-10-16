-- 0012_orchestrator_flags.sql — Phase 2 / PR 2: rollout flag, frozen per application.
--
-- jobs.orchestrator_mode is the per-job rollout flag (design note §6):
--   off    — every application on the job uses the legacy direct-mutation path
--   shadow — threads run alongside the legacy path with effects disabled (PR 5)
--   live   — new applications on the job are driven by the graph (PR 5)
-- It is OFF everywhere; nothing in PR 2 reads it. applications.orchestrated is
-- the snapshot taken at thread start: flipping the job flag later never changes
-- an existing application.

alter table jobs add column if not exists orchestrator_mode text not null default 'off';
alter table jobs drop constraint if exists jobs_orchestrator_mode_check;
alter table jobs add constraint jobs_orchestrator_mode_check
  check (orchestrator_mode in ('off', 'shadow', 'live'));

alter table applications add column if not exists orchestrated boolean not null default false;
alter table applications add column if not exists graph_version integer;

create index if not exists applications_orchestrated_idx
  on applications(orchestrated) where orchestrated;
