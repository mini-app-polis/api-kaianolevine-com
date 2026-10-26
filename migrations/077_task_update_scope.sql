-- Migration 077: a source updating a task it asked for
--
-- POST /v1/tasks/updates puts a task.update on task-manager-cog's queue: a
-- comment on the task, and optionally a move to Review. A machine may only
-- name a task under its own sources (identity_registry.py), so this grants
-- nothing beyond the tasks it could already ask for.
--
-- tasks.requests.update
--   held by task-requester → may comment on its own tasks and move them to
--   Review

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('task-requester', 'tasks.requests.update')
ON CONFLICT (role_name, scope) DO NOTHING;
