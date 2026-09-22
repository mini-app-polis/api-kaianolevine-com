-- Migration 033: the role that asks transcription-cog to run
--
-- watcher-cog notices a file landing in a watched Drive folder and asks
-- for transcription-cog to process it. It used to do that by calling
-- Prefect's create_flow_run for the whole folder; it now POSTs
-- /v1/transcription/runs once per file, and the API enqueues onto
-- transcription-jobs.
--
-- The scope asks for a run and nothing else. It cannot write a transcript
-- or a source — wcs-writer stays with transcription-cog, which does the
-- work — so a leaked watcher key can cause a file to be processed and cannot
-- forge the result.

INSERT INTO identity_roles (name, description) VALUES
  ('transcription-trigger', 'May ask transcription-cog to run.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('transcription-trigger', 'transcription.runs.create')
ON CONFLICT (role_name, scope) DO NOTHING;
