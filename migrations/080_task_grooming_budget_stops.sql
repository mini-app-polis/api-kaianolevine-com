-- Migration 080: grooming runs stopped by the spent budget
--
-- task-groomer-cog stops before its model call when the month's grooming
-- budget is spent. The task's content was sent and not groomed, so the
-- sweep must send it again once there is budget, without waiting for the
-- content to change. task_grooming_budget_stops holds that stop: one row
-- per task, with the fingerprint of the content the stopped job was sent
-- for. A later stop for the same task replaces it.
--
-- It is its own table, not a column on task_groomings, so a stop can be
-- marked before a sweep has written the task's record. The records are
-- served with it, and a won claim for the same task and content clears it.
--
-- tasks.groomings.write, held by task-groomer, covers marking a stop.

CREATE TABLE IF NOT EXISTS task_grooming_budget_stops (
  task_gid     TEXT        PRIMARY KEY,
  fingerprint  TEXT        NOT NULL,
  marked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
