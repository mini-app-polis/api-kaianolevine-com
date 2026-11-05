-- Migration 089: the grooming names go
--
-- Migration 088 renamed grooming to refining and kept the old names for the
-- release still running: views over the renamed tables, the task-groomer
-- role and its tasks.groomings.* scopes, and the needs_grooming agent-run
-- status. Every cog now runs its refining release, so they go.
--
-- An agent run recorded as needs_grooming is the same as needs_refining, so
-- those rows take the new name before the status check stops allowing the
-- old one.

DROP VIEW IF EXISTS task_groomings;
DROP VIEW IF EXISTS task_grooming_runs;
DROP VIEW IF EXISTS task_grooming_claims;
DROP VIEW IF EXISTS task_grooming_budget_stops;

-- Grants first: a principal's role is RESTRICTed, so the role cannot go
-- while one is held. The role's scopes go with it (CASCADE).
DELETE FROM identity_principal_roles WHERE role_name = 'task-groomer';
DELETE FROM identity_roles WHERE name = 'task-groomer';

UPDATE task_agent_runs SET status = 'needs_refining' WHERE status = 'needs_grooming';
ALTER TABLE task_agent_runs DROP CONSTRAINT IF EXISTS task_agent_runs_status_check;
ALTER TABLE task_agent_runs ADD CONSTRAINT task_agent_runs_status_check
  CHECK (status IN ('dispatched', 'succeeded', 'failed', 'capped', 'needs_refining'));
