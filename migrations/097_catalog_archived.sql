-- Migration 097: archived catalog entries
--
-- A catalog entry is archived, never deleted. archived_at is set when an
-- entry is left with no plays and no present library file (a library entry
-- whose only files have gone from the library), and cleared when a present
-- file links to it again. The entry, its aliases and its revisions stay.

ALTER TABLE track_catalog
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
