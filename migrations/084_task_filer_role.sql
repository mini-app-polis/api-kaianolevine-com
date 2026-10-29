-- Migration 084: the role that may file a task and do nothing else
--
-- A Claude session files a manual task (MINI-) through POST /v1/tasks,
-- under the claude-session machine (identity_registry.py), which owns the
-- manual source and no other. task-requester also updates a source's tasks
-- (tasks.requests.update); a session needs only to file, so it holds this
-- narrower role instead.
--
-- tasks.requests.create
--   held by task-filer → may ask for a task on the board, under its own
--   sources. It cannot comment on, move, edit or read a task.

INSERT INTO identity_roles (name, description) VALUES
  ('task-filer', 'May ask task-manager-cog for a task on the board, and nothing else.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('task-filer', 'tasks.requests.create')
ON CONFLICT (role_name, scope) DO NOTHING;
