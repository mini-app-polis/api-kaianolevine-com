-- Migration 086: the role that may ask for a sweep of the board, and nothing else
--
-- POST /v1/tasks/sweeps puts a task.sweep on task-manager-cog's queue now.
-- A Claude session working with the owner asks for one through the
-- request-sweep command, under the claude-session machine, and
-- task-manager-cog's sweep-now workflow under the task-manager-sweep machine
-- (identity_registry.py). wcs-admin holds the scope for a person (migration
-- 071); a machine needs a role of its own, so a key used from the owner's
-- machine can ask for a sweep without holding anything else of the admin's.
-- A sweep asked for this way is held to every switch and spending cap the
-- scheduled one is.
--
-- tasks.sweeps.create
--   held by sweep-requester → may ask for a sweep of the board

INSERT INTO identity_roles (name, description) VALUES
  ('sweep-requester', 'May ask task-manager-cog for a sweep of the board now, and nothing else.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('sweep-requester', 'tasks.sweeps.create')
ON CONFLICT (role_name, scope) DO NOTHING;
