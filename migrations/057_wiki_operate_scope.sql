-- Migration 057: the admin's wiki operations scope
--
-- The /admin/wiki panel starts curator work — rebuilding page documents —
-- through POST /v1/wcs/wiki/admin/jobs, as the signed-in admin, instead of a
-- terminal command. That route needs a scope of its own:
--
-- wcs.wiki.write is the curator's, for writing its state and enqueueing its
-- follow-on jobs; granting it to a human would let the admin overwrite that
-- state too. wcs.corpus.read is reading, and starting work is not reading.
--
-- wcs.wiki.operate
--   held by wcs-admin → may start the free wiki operations (no model calls)

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('wcs-admin', 'wcs.wiki.operate')
ON CONFLICT (role_name, scope) DO NOTHING;
