-- Migration 099: the owner edits the catalog
--
-- The catalog editor on the WCS site edits entries' parts and owned fields
-- and the display rules (deejay-cog ADR-0008, decisions 7 and 11). Those
-- routes need catalog.tracks.write, which only the catalog-ingest machine
-- held. A catalog-editor role grants it to a human: every human principal
-- holding wcs-admin (the owner). Machine principals keep their own roles.
--
-- catalog_revisions gains the kind 'edit': every edit through the API
-- records the entry's values before and after.

INSERT INTO identity_roles (name, description) VALUES
  ('catalog-editor', 'Human who edits catalog entries and the display rules.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('catalog-editor', 'catalog.tracks.write')
ON CONFLICT (role_name, scope) DO NOTHING;

INSERT INTO identity_principal_roles (principal_id, role_name, granted_by)
SELECT pr.principal_id, 'catalog-editor', 'migration_099'
FROM identity_principal_roles pr
JOIN identity_principals ip ON ip.id = pr.principal_id
WHERE pr.role_name = 'wcs-admin' AND ip.kind = 'human'
ON CONFLICT (principal_id, role_name) DO NOTHING;

ALTER TABLE catalog_revisions DROP CONSTRAINT IF EXISTS catalog_revisions_kind_check;
ALTER TABLE catalog_revisions ADD CONSTRAINT catalog_revisions_kind_check
  CHECK (kind IN ('genre_map', 'split_parts', 'merge', 'edit'));
