-- Migration 095: catalog clean-up revisions
--
-- The catalog clean-up commands (genre mapping, splitting entries into
-- their parts) change entries in bulk, and only after the owner has reviewed
-- a dry run. Each change they apply is recorded here with the entry's values
-- before and after, so any of them can be put back.
--
-- kind names the command that made the change (genre_map, split_parts).

CREATE TABLE IF NOT EXISTS catalog_revisions (
  id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_id  UUID         NOT NULL REFERENCES track_catalog (id),
  kind        TEXT         NOT NULL CHECK (kind IN ('genre_map', 'split_parts')),
  before      JSONB        NOT NULL,
  after       JSONB        NOT NULL,
  applied_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_catalog_revisions_catalog_id
  ON catalog_revisions (catalog_id, applied_at);
