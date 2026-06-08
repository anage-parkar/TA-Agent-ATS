-- Manual pipeline stage for an application (set by dragging a Kanban card).
-- When null, the stage is derived from `status`.
alter table applications add column if not exists stage text;
