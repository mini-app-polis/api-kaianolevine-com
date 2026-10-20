-- Migration 064: the role that may ask for a task on the board
--
-- POST /v1/tasks enqueues a request for task-manager-cog, the one writer to
-- the task board. Sources of tasks are granted this role in
-- identity_registry; the API's own GitHub issue path calls the dispatcher
-- in-process and needs no grant.
--
-- tasks.requests.create
--   held by task-requester → may ask for a task on the board. Asking cannot
--   edit, move or read tasks, and asking twice for one external_id makes one
--   task.

INSERT INTO identity_roles (name, description) VALUES
  ('task-requester', 'May ask task-manager-cog for a task on the board.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('task-requester', 'tasks.requests.create')
ON CONFLICT (role_name, scope) DO NOTHING;
