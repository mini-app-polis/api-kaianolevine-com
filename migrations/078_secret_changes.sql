-- Migration 078: secret names Doppler reports changed
--
-- Doppler's config.secrets.update webhook (POST /v1/webhooks/doppler) lists
-- the names added, removed and updated in a config, never values. Each
-- delivery is stored once, by a hash of its body, because Doppler may send
-- one more than once; each name in it is one secret_changes row. rotator-cog's
-- sweep compares these with rotation_events, so a value changed outside the
-- rotation process is found.
--
-- No column holds a secret value or the delivery's body: names, configs and
-- times only, checked as rotation_events' are.
--
-- secrets.changes.read
--   held by secrets-rotator → the sweep reads the changes
--   held by wcs-admin → the owner can see them

CREATE TABLE IF NOT EXISTS doppler_deliveries (
  id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  -- SHA-256 of the delivery's raw body: a retry has the same one.
  body_sha256  TEXT         NOT NULL UNIQUE CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
  received_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS secret_changes (
  id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  delivery_id  UUID         NOT NULL REFERENCES doppler_deliveries (id),
  name         TEXT         NOT NULL
                 CHECK (name ~ '^[A-Z][A-Z0-9_]*$' AND length(name) <= 128),
  config       TEXT         NOT NULL CHECK (config IN ('prd', 'dev')),
  change       TEXT         NOT NULL CHECK (change IN ('added', 'removed', 'updated')),
  changed_at   TIMESTAMPTZ  NOT NULL,
  UNIQUE (delivery_id, name, change)
);

CREATE INDEX IF NOT EXISTS ix_secret_changes_config_changed_at
  ON secret_changes (config, changed_at);

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('secrets-rotator', 'secrets.changes.read'),
  ('wcs-admin', 'secrets.changes.read')
ON CONFLICT (role_name, scope) DO NOTHING;
