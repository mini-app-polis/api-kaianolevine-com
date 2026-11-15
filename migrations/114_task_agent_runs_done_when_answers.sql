-- Migration 114: a succeeded agent run's answers to its task's Done when
--
-- The run's workflow posts one answer per Done when item, in the task's
-- order, each naming its evidence. task-manager-cog writes them into the
-- task's Handed off: comment as a numbered list, which the Review check
-- reads item by item. done_when_answers is empty for every other outcome,
-- and for a run that gave none.

ALTER TABLE task_agent_runs
  ADD COLUMN IF NOT EXISTS done_when_answers JSONB NOT NULL DEFAULT '[]'::jsonb;
