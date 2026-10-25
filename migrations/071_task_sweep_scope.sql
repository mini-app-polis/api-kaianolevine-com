-- Migration 071: the admin's scope for asking for a sweep of the board
--
-- POST /v1/tasks/sweeps puts a task.sweep on task-manager-cog's queue now,
-- the message its hourly schedule sends, so the admin need not wait for the
-- next hour. A sweep can send tasks for paid grooming and dispatch agent
-- runs, each held to its monthly cap, so it has a scope of its own.
-- tasks.requests.create is the sources' and puts a task on the board; it is
-- not granted here.
--
-- tasks.sweeps.create
--   held by wcs-admin → may ask for a sweep of the board

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('wcs-admin', 'tasks.sweeps.create')
ON CONFLICT (role_name, scope) DO NOTHING;
