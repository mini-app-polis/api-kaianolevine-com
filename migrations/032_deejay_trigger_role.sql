-- Migration 032: the role that asks deejay-cog to run
--
-- watcher-cog notices a file landing in a watched Drive folder and asks
-- for deejay-cog to process it. It used to do that by calling Prefect's
-- create_flow_run; it now POSTs /v1/deejay/runs, and the API enqueues
-- onto deejay-jobs.
--
-- The scope asks for a run and nothing else. It cannot write to the
-- catalog — catalog-ingest stays with deejay-cog, which does the work — so
-- a leaked watcher key can cause a folder sweep and cannot forge its
-- result.

INSERT INTO identity_roles (name, description) VALUES
  ('deejay-trigger', 'May ask deejay-cog to run.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('deejay-trigger', 'deejay.runs.create')
ON CONFLICT (role_name, scope) DO NOTHING;
