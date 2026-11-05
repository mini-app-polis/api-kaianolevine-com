-- Migration 091: the role that may ask for the API's quiet sweep of faults
--
-- A 502, 503 or 504 this service answers is a dependency failing, and its
-- GitHub Issue closes once the route has gone SIGNAL_FAULT_QUIET_HOURS
-- without another (services/fault_signals.py). watcher-cog asks for that
-- check on a schedule, POST /v1/signals/api-faults/quiet-sweep, under its
-- own key (identity_registry.py).
--
-- The scope runs the sweep and nothing else: it can close a dependency
-- fault that has already gone quiet, and cannot open, close or comment on
-- any other Issue, or close a 500.
--
-- signals.faults.sweep
--   held by fault-sweeper → may ask for the quiet sweep of API faults

INSERT INTO identity_roles (name, description) VALUES
  ('fault-sweeper', 'May ask the API to close its dependency faults that have gone quiet, and nothing else.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('fault-sweeper', 'signals.faults.sweep')
ON CONFLICT (role_name, scope) DO NOTHING;
