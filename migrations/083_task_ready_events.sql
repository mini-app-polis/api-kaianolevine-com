-- Migration 083: moves to Ready and returns from it, as they happen
--
-- task_groomings holds one task's latest state: when the sweep last moved it
-- to Ready (moved_to_ready_at) and how many times it has come back out
-- (returned_count). Neither says when a return happened, so a ratio over a
-- window cannot be read from it. task_ready_events records each one as the
-- record is written: a moved_to_ready event when moved_to_ready_at is set to
-- a new time, a returned event for each step returned_count goes up.
--
-- GET /v1/tasks/groomings/return-rate counts them over a window. Events
-- start with this migration, so a window reaching back before it counts
-- from when it was applied (schema_migrations.applied_at), and says so.

CREATE TABLE IF NOT EXISTS task_ready_events (
  id           BIGSERIAL   PRIMARY KEY,
  task_gid     TEXT        NOT NULL,
  kind         TEXT        NOT NULL CHECK (kind IN ('moved_to_ready', 'returned')),
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_task_ready_events_occurred_at
  ON task_ready_events (occurred_at);
