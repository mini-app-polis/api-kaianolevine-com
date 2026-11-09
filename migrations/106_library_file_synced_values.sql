-- Migration 106: the owned fields a library file was last synced with
--
-- deejay-cog's library sync writes to a linked file only the owned fields
-- (title, artist, year, genre, BPM, label) whose catalog value moved since
-- the file was last synced, so a value the owner held in the reconcile is
-- not overwritten by an edit to another field of its entry (deejay-cog
-- ADR-0008). synced_values holds those fields as the catalog showed them at
-- the last sync; NULL until a sync records them, and cleared when identify or
-- the reconcile registers the file as synced, so the next sync records them
-- afresh before it writes anything.

ALTER TABLE library_files
  ADD COLUMN IF NOT EXISTS synced_values JSONB;
