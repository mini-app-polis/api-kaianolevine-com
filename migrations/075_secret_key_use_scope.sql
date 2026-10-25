-- Migration 075: reading when a machine last used its previous key
--
-- GET /v1/secrets/key-use reports, from identity_audit_events.key_slot
-- (migration 073), the newest request a machine proved with its previous
-- key. rotator-cog retires a previous key once that is a day old.
--
-- secrets.key-use.read
--   held by secrets-rotator → may read it for retirement
--   held by wcs-admin → may read it to see where a rotation stands

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('secrets-rotator', 'secrets.key-use.read'),
  ('wcs-admin', 'secrets.key-use.read')
ON CONFLICT (role_name, scope) DO NOTHING;
