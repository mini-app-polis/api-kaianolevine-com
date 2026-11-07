-- Migration 098: one catalog
--
-- The catalog was per owner: an entry's matching keys were unique per
-- owner_id, so the same song could hold an entry under each key that filed
-- its plays. Duplicate entries are merged by
-- `python -m kaianolevine_api.cleanup.merge` after the owner's review; a
-- merged entry is archived (archived_at, migration 097), never deleted.
--
-- Once no two live entries share their keys, the keys are unique across
-- the whole catalog: the per-owner constraint goes and a unique index on
-- the keys of live entries takes its place. On a database that still holds
-- duplicates nothing changes here; the merge command makes the same change
-- once its last group is applied (cleanup.merge.ONE_CATALOG_SQL, the same
-- statements as below).
--
-- catalog_revisions gains the kind 'merge', which the merge command
-- records each merged entry's values under, before and after.

ALTER TABLE catalog_revisions DROP CONSTRAINT IF EXISTS catalog_revisions_kind_check;
ALTER TABLE catalog_revisions ADD CONSTRAINT catalog_revisions_kind_check
  CHECK (kind IN ('genre_map', 'split_parts', 'merge'));

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM track_catalog
    WHERE archived_at IS NULL
    GROUP BY title_normalized, artist_normalized
    HAVING count(*) > 1
  ) THEN
    ALTER TABLE track_catalog
      DROP CONSTRAINT IF EXISTS uq_track_catalog_owner_norm_title_artist;
    CREATE UNIQUE INDEX IF NOT EXISTS ux_track_catalog_norm_title_artist
      ON track_catalog (title_normalized, artist_normalized)
      WHERE archived_at IS NULL;
  END IF;
END $$;
