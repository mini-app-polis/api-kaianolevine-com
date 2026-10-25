-- Migration 072: the record of secret rotations
--
-- rotator-cog mints a new value for one secret in one Doppler config, keeps
-- the old value valid beside it for a while, and then retires the old one.
-- rotation_events holds one row per rotation, keyed on the secret's name, the
-- config and when the new value was minted: the rotator writes the row once
-- the new value has been verified (or has failed verification), and again
-- with retired once the old value is gone. These rows are the only record of
-- what was rotated, and the daily sweep reads the latest per name and config
-- to decide what is due. Nothing deletes one.
--
-- No column holds a secret value. Each one is a fixed set, a pattern a value
-- does not fit, a time, or a bounded sentence, and the checks below refuse
-- what the contract's models refuse, whoever writes the row.
--
-- secrets.rotation-events.read / secrets.rotation-events.write
--   held by secrets-rotator → may record rotations and read the record.
--   held by wcs-admin → the owner's hand-run rotations record themselves
--   under his own identity.
--   Neither starts a rotation.

CREATE TABLE IF NOT EXISTS rotation_events (
  id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  name                 TEXT         NOT NULL
                         CHECK (name ~ '^[A-Z][A-Z0-9_]*$' AND length(name) <= 128),
  config               TEXT         NOT NULL CHECK (config IN ('prd', 'dev')),
  kind                 TEXT         NOT NULL
                         CHECK (kind IN ('machine-key', 'generated', 'aws-access-key', 'manual')),
  mode                 TEXT         NOT NULL CHECK (mode IN ('routine', 'compromised')),
  -- A machine's name or a person's email.
  actor                TEXT         NOT NULL
                         CHECK (actor ~ '^[A-Za-z0-9._%+@-]+$' AND length(actor) <= 254),
  minted               TIMESTAMPTZ  NOT NULL,
  retired              TIMESTAMPTZ,
  verification         TEXT         NOT NULL CHECK (verification IN ('ok', 'failed')),
  -- Why verification failed, in a sentence: never the value.
  verification_reason  TEXT         CHECK (length(verification_reason) <= 500),
  -- The last four characters of an AWS access key's id, never its secret.
  aws_key_last4        TEXT         CHECK (aws_key_last4 ~ '^[A-Z0-9]{4}$'),
  recorded_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT rotation_events_retired_after_minted
    CHECK (retired IS NULL OR retired >= minted),
  CONSTRAINT rotation_events_reason_only_when_failed
    CHECK (verification_reason IS NULL OR verification = 'failed'),
  CONSTRAINT rotation_events_aws_key_only_for_aws
    CHECK (aws_key_last4 IS NULL OR kind = 'aws-access-key'),
  UNIQUE (name, config, minted)
);

CREATE INDEX IF NOT EXISTS ix_rotation_events_minted
  ON rotation_events (minted DESC);

INSERT INTO identity_roles (name, description) VALUES
  ('secrets-rotator', 'May record secret rotations and read the record.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('secrets-rotator', 'secrets.rotation-events.read'),
  ('secrets-rotator', 'secrets.rotation-events.write'),
  ('wcs-admin', 'secrets.rotation-events.read'),
  ('wcs-admin', 'secrets.rotation-events.write')
ON CONFLICT (role_name, scope) DO NOTHING;
