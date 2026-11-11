-- Migration 112: each catalog entry belongs to one library, main or fx
--
-- The library spans two roots (deejay-cog ADR-0009): the main library and
-- FX Music, the fx and bumper music for events. FX files are in the
-- catalog but never on the main library's entries, even when the song is
-- the same: an FX file has an fx entry of its own, and a main-library file
-- never links to an fx entry. The API refuses a link across the two.
--
-- Every existing entry is main. A song may have an entry in each library,
-- so the keys are unique per library: the one-catalog index (migration
-- 098), or the per-owner key on a database that still holds duplicates,
-- gains the library. With only main entries, what it allows is unchanged.

ALTER TABLE track_catalog
  ADD COLUMN IF NOT EXISTS library TEXT NOT NULL DEFAULT 'main'
    CHECK (library IN ('main', 'fx'));

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'ux_track_catalog_norm_title_artist'
  ) THEN
    DROP INDEX ux_track_catalog_norm_title_artist;
    CREATE UNIQUE INDEX ux_track_catalog_norm_title_artist
      ON track_catalog (library, title_normalized, artist_normalized)
      WHERE archived_at IS NULL;
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'uq_track_catalog_owner_norm_title_artist'
  ) THEN
    ALTER TABLE track_catalog
      DROP CONSTRAINT uq_track_catalog_owner_norm_title_artist;
    ALTER TABLE track_catalog
      ADD CONSTRAINT uq_track_catalog_owner_norm_title_artist
      UNIQUE (owner_id, library, title_normalized, artist_normalized);
  END IF;
END $$;
