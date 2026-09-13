-- Migration 029: the role that asks for a repository to be evaluated
--
-- A repository's CI calls POST /v1/evaluations/runs when it releases, and
-- the API forwards the job to evaluator-cog. The caller is a GitHub Actions
-- run, not a cog: it holds an organisation-level secret every repository in
-- the fleet can read, which is precisely why it gets a role of its own
-- rather than borrowing one.
--
-- The scope asks for an evaluation and nothing else. It cannot write
-- findings — pipeline.evaluations.write stays with the machines that
-- actually produce them — so a leaked CI key can cause work to happen and
-- cannot forge its result.

INSERT INTO identity_roles (name, description) VALUES
  ('evaluation-trigger', 'May ask for a repository to be evaluated.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('evaluation-trigger', 'evaluations.runs.create')
ON CONFLICT (role_name, scope) DO NOTHING;
