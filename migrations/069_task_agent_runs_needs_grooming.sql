-- Migration 069: agent runs that send their task back to grooming
--
-- A run that finds its task unclear or undetermined changes nothing and
-- ends as needs_grooming, with the questions it could not settle.
-- task-manager-cog moves the task back to Refining with them. questions is
-- empty for every other outcome.

ALTER TABLE task_agent_runs DROP CONSTRAINT IF EXISTS task_agent_runs_status_check;
ALTER TABLE task_agent_runs ADD CONSTRAINT task_agent_runs_status_check
  CHECK (status IN ('dispatched', 'succeeded', 'failed', 'capped', 'needs_grooming'));

ALTER TABLE task_agent_runs
  ADD COLUMN IF NOT EXISTS questions JSONB NOT NULL DEFAULT '[]'::jsonb;
