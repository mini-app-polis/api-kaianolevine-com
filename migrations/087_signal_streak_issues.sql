-- Migration 087: the GitHub Issue a streak opened
--
-- An escalated streak opens a GitHub Issue in the repository it is about
-- instead of asking for a board task (services/signals.py). The Issue is
-- recorded here so further failures comment on it and the streak's recovery
-- closes it. Streaks escalated before this keep their task_external_id and
-- no Issue.

ALTER TABLE signal_streaks
  ADD COLUMN IF NOT EXISTS issue_repo   TEXT,
  ADD COLUMN IF NOT EXISTS issue_number INTEGER;
