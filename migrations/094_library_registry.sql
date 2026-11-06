-- Migration 094: the music library registry
--
-- One row per Drive file in the music library: where it is (its library
-- root and path), what it is (size, checksum, duration), the catalog entry
-- it is linked to, and whether it is still there. deejay-cog registers the
-- files it reaches and marks the ones gone from the library not present; a
-- row is never deleted, so a removed file's link and history stay.
--
-- An identified file is linked to a catalog entry; an unidentified one has
-- no link and waits for the owner to identify it. The check holds the two
-- together.
--
-- A song in the library that has never been played gets a catalog entry of
-- its own: source 'library', play_count 0, no first or last play. Stats,
-- summaries and the collection count plays, so those entries are not in
-- them.
--
-- The library is one root today. library_root_id records which root each
-- file is under, so a second root needs no change here.
--
-- catalog.library.write
--   held by catalog-library → deejay-cog registers files, marks them gone,
--   lists them, and makes library entries

CREATE TABLE IF NOT EXISTS library_files (
  id                     UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  drive_file_id          TEXT         NOT NULL UNIQUE CHECK (drive_file_id <> ''),
  library_root_id        TEXT         NOT NULL CHECK (library_root_id <> ''),
  path                   TEXT         NOT NULL CHECK (path <> ''),
  size_bytes             BIGINT       CHECK (size_bytes >= 0),
  md5                    TEXT,
  duration_secs          DOUBLE PRECISION CHECK (duration_secs >= 0),
  catalog_id             UUID         REFERENCES track_catalog (id),
  edit_label             TEXT,
  comment_backup         TEXT,
  identification_status  TEXT         NOT NULL
                           CHECK (identification_status IN ('identified', 'unidentified')),
  present                BOOLEAN      NOT NULL DEFAULT TRUE,
  trash_requested        BOOLEAN      NOT NULL DEFAULT FALSE,
  first_seen             TIMESTAMPTZ  NOT NULL DEFAULT now(),
  last_seen              TIMESTAMPTZ  NOT NULL DEFAULT now(),
  last_synced            TIMESTAMPTZ,
  CONSTRAINT ck_library_files_linked_when_identified
    CHECK ((identification_status = 'identified') = (catalog_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_library_files_catalog_id
  ON library_files (catalog_id);
CREATE INDEX IF NOT EXISTS ix_library_files_status_present
  ON library_files (identification_status, present);

INSERT INTO identity_roles (name, description) VALUES
  ('catalog-library', 'Machine that registers the music library''s files and makes catalog entries for songs in it.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('catalog-library', 'catalog.library.write')
ON CONFLICT (role_name, scope) DO NOTHING;
