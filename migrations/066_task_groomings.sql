-- Migration 066: the task board's grooming records
--
-- task-manager-cog sweeps the board every hour and checks each waiting task
-- against the definition of ready. task_groomings is what it remembers about
-- one task between sweeps: a fingerprint of the content it last judged (so a
-- later sweep can tell a task someone changed from one still waiting), the
-- verdict, and whether it moved the task to Ready. returned_count counts the
-- times a task it moved to Ready was moved back out, which is how to tell
-- whether ready meant ready.
--
-- task_grooming_runs holds one row per paid grooming run (a model reading a
-- task and proposing its fields), so the month's spend can be capped.
--
-- tasks.groomings.read / tasks.groomings.write
--   held by task-groomer → may read and write these records. They say
--   nothing about the board itself, which only task-manager-cog writes.

CREATE TABLE IF NOT EXISTS task_groomings (
  task_gid           TEXT        PRIMARY KEY,
  task_id            TEXT,
  fingerprint        TEXT        NOT NULL,
  outcome            TEXT        NOT NULL
                       CHECK (outcome IN ('ready', 'not_ready', 'questions')),
  problems           JSONB       NOT NULL DEFAULT '[]'::jsonb,
  moved_to_ready_at  TIMESTAMPTZ,
  returned_count     INTEGER     NOT NULL DEFAULT 0 CHECK (returned_count >= 0),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS task_grooming_runs (
  id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  task_gid       TEXT          NOT NULL,
  model          TEXT          NOT NULL,
  input_tokens   INTEGER       NOT NULL CHECK (input_tokens >= 0),
  output_tokens  INTEGER       NOT NULL CHECK (output_tokens >= 0),
  cost_usd       NUMERIC(10,4) NOT NULL CHECK (cost_usd >= 0),
  recorded_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_task_grooming_runs_recorded_at
  ON task_grooming_runs (recorded_at);

INSERT INTO identity_roles (name, description) VALUES
  ('task-groomer', 'May read and write the task board''s grooming records.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('task-groomer', 'tasks.groomings.read'),
  ('task-groomer', 'tasks.groomings.write')
ON CONFLICT (role_name, scope) DO NOTHING;
