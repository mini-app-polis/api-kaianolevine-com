-- Migration 082: rotation incidents
--
-- An incident is a compromised rotation of every secret in the rotator's
-- inventory, in both configs, declared at once by the owner and tracked
-- under one id. POST /v1/secrets/rotation-incidents mints the id, records
-- when the incident started, and queues one rotation.incident for
-- rotator-cog. The rotator registers the incident's entries (the inventory
-- as it stood when the incident was declared): each secret in each config,
-- its tier, and whether the rotator rotates it or a person does from a
-- board task. It marks an entry once that task has been asked for. Each
-- rotation it records for the incident carries the incident's id.
--
-- An entry's state is never stored: GET /v1/secrets/rotation-incidents/{id}
-- derives it from the rotation events minted after the incident started
-- (rotated, failed) and the task flag (task-opened), else outstanding. So a
-- rotation recorded any other way after the start, scripts/rotate.py's
-- included, counts.
--
-- This API keeps no copy of the rotator's inventory: the registered entries
-- are the only inventory it knows. No column holds a secret value.
--
-- secrets.rotation-incidents.write
--   held by secrets-rotator → may register an incident's entries and mark
--   an entry's task as asked for. Starting an incident stays
--   secrets.rotations.create, which no machine holds (migration 074).
-- secrets.rotation-incidents.read
--   held by secrets-rotator → reads what is done when an incident resumes
--   held by wcs-admin → reads where an incident stands

CREATE TABLE IF NOT EXISTS rotation_incidents (
  id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  started_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rotation_incident_entries (
  incident_id     UUID     NOT NULL REFERENCES rotation_incidents (id) ON DELETE CASCADE,
  name            TEXT     NOT NULL
                    CHECK (name ~ '^[A-Z][A-Z0-9_]*$' AND length(name) <= 128),
  config          TEXT     NOT NULL CHECK (config IN ('prd', 'dev')),
  tier            SMALLINT NOT NULL CHECK (tier BETWEEN 0 AND 3),
  handling        TEXT     NOT NULL CHECK (handling IN ('worker', 'task')),
  -- The order the rotator registered the entries in.
  position        INTEGER  NOT NULL CHECK (position >= 0),
  task_requested  BOOLEAN  NOT NULL DEFAULT false,
  PRIMARY KEY (incident_id, name, config)
);

-- The rotations of an incident. No foreign key: the record of a rotation is
-- never refused, and an id the contract's pattern accepts is enough.
ALTER TABLE rotation_events ADD COLUMN IF NOT EXISTS incident_id UUID;

CREATE INDEX IF NOT EXISTS ix_rotation_events_incident
  ON rotation_events (incident_id) WHERE incident_id IS NOT NULL;

-- The read looks for events of an entry minted after its incident started.
CREATE INDEX IF NOT EXISTS ix_rotation_events_name_config_minted
  ON rotation_events (name, config, minted DESC);

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('secrets-rotator', 'secrets.rotation-incidents.write'),
  ('secrets-rotator', 'secrets.rotation-incidents.read'),
  ('wcs-admin', 'secrets.rotation-incidents.read')
ON CONFLICT (role_name, scope) DO NOTHING;
