-- Migration 101: a library file's own tags, and why it is unidentified
--
-- The owner's unidentified-files page shows each file's own tags as the
-- nightly run last read them, and why it could not be identified (with the
-- fingerprint's best guess when it had one). Both are sent by deejay-cog
-- with the registration and kept until it sends them again.

ALTER TABLE library_files
  ADD COLUMN IF NOT EXISTS tags JSONB,
  ADD COLUMN IF NOT EXISTS identify_note TEXT;
