-- Migration 088: grooming is called refining
--
-- The board's column is Refining, and the code now says so too. The four
-- tables become task_refinings, task_refining_runs, task_refining_claims and
-- task_refining_budget_stops, and task_refinings' columns groomed_fingerprint,
-- groom_sent_at and groom_attempts become refined_fingerprint, refine_sent_at
-- and refine_attempts. Nothing is copied: the rename keeps every row.
--
-- The old names stay readable and writable as views over the renamed
-- tables, so the release still running while this one starts (which runs
-- this migration before it serves) keeps working until it stops. They are
-- dropped with the grooming routes, once every cog has moved to refining.
--
-- tasks.refinings.read / tasks.refinings.write
--   held by task-refiner → may read and write these records. task-groomer
--   and its tasks.groomings.* scopes stay for the /v1/tasks/groomings
--   routes, served beside the refining ones until every cog has moved.
--
-- An agent run that sends its task back to refining ends as needs_refining;
-- needs_grooming, its old name, is still accepted.

DO $$
BEGIN
  IF to_regclass('task_refinings') IS NULL THEN
    ALTER TABLE task_groomings RENAME TO task_refinings;
    ALTER TABLE task_refinings RENAME COLUMN groomed_fingerprint TO refined_fingerprint;
    ALTER TABLE task_refinings RENAME COLUMN groom_sent_at TO refine_sent_at;
    ALTER TABLE task_refinings RENAME COLUMN groom_attempts TO refine_attempts;
  END IF;
  IF to_regclass('task_refining_runs') IS NULL THEN
    ALTER TABLE task_grooming_runs RENAME TO task_refining_runs;
    ALTER INDEX IF EXISTS ix_task_grooming_runs_recorded_at
      RENAME TO ix_task_refining_runs_recorded_at;
  END IF;
  IF to_regclass('task_refining_claims') IS NULL THEN
    ALTER TABLE task_grooming_claims RENAME TO task_refining_claims;
  END IF;
  IF to_regclass('task_refining_budget_stops') IS NULL THEN
    ALTER TABLE task_grooming_budget_stops RENAME TO task_refining_budget_stops;
  END IF;
END
$$;

-- The old names, for the release still running. Simple views over one table
-- are updatable, including INSERT ... ON CONFLICT. A view column takes no
-- default from its table, so the defaults a writer leaves to the table are
-- set on the view as well.
CREATE OR REPLACE VIEW task_groomings AS
  SELECT task_gid, task_id, fingerprint, outcome, problems, moved_to_ready_at,
         returned_count, updated_at,
         refined_fingerprint AS groomed_fingerprint,
         refine_sent_at      AS groom_sent_at,
         refine_attempts     AS groom_attempts
  FROM task_refinings;
ALTER VIEW task_groomings ALTER COLUMN problems SET DEFAULT '[]'::jsonb;
ALTER VIEW task_groomings ALTER COLUMN returned_count SET DEFAULT 0;
ALTER VIEW task_groomings ALTER COLUMN updated_at SET DEFAULT now();
ALTER VIEW task_groomings ALTER COLUMN groom_attempts SET DEFAULT 0;

CREATE OR REPLACE VIEW task_grooming_runs AS SELECT * FROM task_refining_runs;
ALTER VIEW task_grooming_runs ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER VIEW task_grooming_runs ALTER COLUMN recorded_at SET DEFAULT now();

CREATE OR REPLACE VIEW task_grooming_claims AS SELECT * FROM task_refining_claims;
ALTER VIEW task_grooming_claims ALTER COLUMN state SET DEFAULT 'claimed';
ALTER VIEW task_grooming_claims ALTER COLUMN claimed_at SET DEFAULT now();

CREATE OR REPLACE VIEW task_grooming_budget_stops AS
  SELECT * FROM task_refining_budget_stops;
ALTER VIEW task_grooming_budget_stops ALTER COLUMN marked_at SET DEFAULT now();

INSERT INTO identity_roles (name, description) VALUES
  ('task-refiner', 'May read and write the task board''s refining records.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('task-refiner', 'tasks.refinings.read'),
  ('task-refiner', 'tasks.refinings.write')
ON CONFLICT (role_name, scope) DO NOTHING;

ALTER TABLE task_agent_runs DROP CONSTRAINT IF EXISTS task_agent_runs_status_check;
ALTER TABLE task_agent_runs ADD CONSTRAINT task_agent_runs_status_check
  CHECK (status IN (
    'dispatched', 'succeeded', 'failed', 'capped', 'needs_refining', 'needs_grooming'
  ));
