-- Migration 100: duplicate groups in the music library
--
-- deejay-cog's duplicate report sends the files under a library root that
-- look like copies of one song (deejay-cog ADR-0008, decision 7). Each
-- report replaces the root's open groups; a resolved group is kept, with
-- the keeper the owner picked. Resolving merges the group's catalog entries
-- into the keeper's and marks the other files trash-requested in the
-- registry. Nothing is deleted. A member kept on purpose (a different edit
-- or label) is never trash-requested through its group.

CREATE TABLE IF NOT EXISTS library_duplicate_groups (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  library_root_id  TEXT NOT NULL,
  key              TEXT NOT NULL,
  reasons          TEXT[] NOT NULL,
  status           TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'resolved')),
  keeper_drive_file_id TEXT,
  resolved_by      TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  reported_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at      TIMESTAMPTZ,
  CHECK ((status = 'resolved') = (resolved_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_library_duplicate_groups_open_key
  ON library_duplicate_groups (library_root_id, key)
  WHERE status = 'open';

CREATE INDEX IF NOT EXISTS ix_library_duplicate_groups_root_status
  ON library_duplicate_groups (library_root_id, status);

CREATE TABLE IF NOT EXISTS library_duplicate_members (
  group_id         UUID NOT NULL
                   REFERENCES library_duplicate_groups (id) ON DELETE CASCADE,
  drive_file_id    TEXT NOT NULL,
  kept_on_purpose  BOOLEAN NOT NULL DEFAULT false,
  why_kept         TEXT,
  bitrate_kbps     INTEGER,
  PRIMARY KEY (group_id, drive_file_id),
  CHECK (NOT kept_on_purpose OR why_kept IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_library_duplicate_members_file
  ON library_duplicate_members (drive_file_id);
