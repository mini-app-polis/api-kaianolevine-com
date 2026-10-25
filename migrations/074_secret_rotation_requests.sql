-- Migration 074: the admin's scope for asking for a secret rotation
--
-- POST /v1/secrets/rotations puts a rotation.job on rotator-cog's queue: it
-- mints a new value for one secret in one Doppler config, hands it over and
-- records the rotation. It has a scope of its own, so holding the record's
-- scopes (secrets.rotation-events.*) does not let a caller start one, and no
-- machine role is granted it.
--
-- secrets.rotations.create
--   held by wcs-admin → may ask for a secret to be rotated now

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('wcs-admin', 'secrets.rotations.create')
ON CONFLICT (role_name, scope) DO NOTHING;
