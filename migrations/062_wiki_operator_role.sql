-- Migration 062: the role a deploy uses to rebuild the wiki's pages
--
-- A wiki-curator-cog release can change how pages are built without
-- changing any of the curator's state, so nothing it does would rebuild
-- them. Its CI asks for one rebuild of every page after the Lambda deploy,
-- through POST /v1/wcs/wiki/admin/jobs — the same free operation the
-- /admin/wiki panel starts (MINI-109). The API stays the only producer onto
-- the curator's queue (CD-006).
--
-- The caller is a GitHub Actions run holding a repository-level secret, so
-- it gets a role of its own rather than borrowing wcs-admin. That role holds
-- wcs.wiki.operate and nothing else: it starts free operations only (no
-- model calls), cannot write the curator's state (wcs.wiki.write), cannot
-- read the corpus (wcs.corpus.read), and cannot start paid work
-- (wcs.notes.author). A leaked key can rebuild pages, which they are anyway
-- after every deploy.
--
-- wcs.wiki.operate
--   held by wcs-admin and wiki-operator → may start the free wiki operations

INSERT INTO identity_roles (name, description) VALUES
  ('wiki-operator', 'May start the free wiki operations, such as rebuilding pages.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('wiki-operator', 'wcs.wiki.operate')
ON CONFLICT (role_name, scope) DO NOTHING;
