-- Migration 068: the agent runs that implement Ready tasks
--
-- task-manager-cog dispatches an agent for a Ready task: a headless Claude
-- Code run in GitHub Actions, in the repository the task names, which opens
-- a draft pull request into dev. task_agent_runs holds one row per dispatch,
-- written before the run is started. A row still 'dispatched' is a run in
-- flight, and the lock that keeps a second run out of its repository. Its
-- result (how it ended, what it cost, the pull request) is recorded once,
-- by the run's workflow, or task-manager-cog marks it failed.
--
-- The month's spend is the sum of cost_usd, for the monthly cap. cost_usd
-- is NULL while a run is in flight, or when it ended without reporting.
--
-- tasks.agent-runs.read / tasks.agent-runs.write
--   held by agent-dispatcher → may record runs, list them, read the month's
--   spend, and mark a run failed.
-- tasks.agent-runs.report
--   held by agent-reporter → may post one run's result, and nothing else.
--   It is the key the agent's workflow holds.

CREATE TABLE IF NOT EXISTS task_agent_runs (
  id               UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  task_gid         TEXT          NOT NULL,
  task_id          TEXT,
  repository       TEXT          NOT NULL,
  model            TEXT          NOT NULL,
  status           TEXT          NOT NULL DEFAULT 'dispatched'
                     CHECK (status IN ('dispatched', 'succeeded', 'failed', 'capped')),
  cost_usd         NUMERIC(10,4) CHECK (cost_usd >= 0),
  pr_url           TEXT,
  actions_run_url  TEXT,
  reason           TEXT,
  dispatched_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
  finished_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_task_agent_runs_dispatched_at
  ON task_agent_runs (dispatched_at);

-- At most one run in flight per repository, whatever the caller checks.
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_agent_runs_in_flight
  ON task_agent_runs (repository) WHERE status = 'dispatched';

INSERT INTO identity_roles (name, description) VALUES
  ('agent-dispatcher', 'May record, list and fail agent runs, and read their spend.'),
  ('agent-reporter', 'May post one agent run''s result.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('agent-dispatcher', 'tasks.agent-runs.read'),
  ('agent-dispatcher', 'tasks.agent-runs.write'),
  ('agent-reporter', 'tasks.agent-runs.report')
ON CONFLICT (role_name, scope) DO NOTHING;
