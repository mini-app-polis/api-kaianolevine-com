-- Migration 034: dispatch claims
--
-- watcher-cog is moving from an always-on Railway loop that remembered what
-- it had seen to a scheduled Lambda that remembers nothing. Every tick lists
-- its folders and asks for whatever is there, so a file still being
-- processed is asked for again every minute until its cog moves it. This
-- table is the memory the watcher no longer has, kept where the fleet's
-- other state lives: one row per file asked for, and a unique key that
-- makes the second ask a no-op rather than a second job.
--
-- Two kinds of claim share the table:
--   * presence  (revision = '')  — a drained inbox. The file being there is
--     the work. The claim expires after the dispatch window, so a file still
--     there afterwards is asked for again, up to a cap.
--   * revision  (revision = the file's modifiedTime) — a file edited in
--     place that never leaves. Each version is claimed once, permanently.
--
-- revision is '' rather than NULL because NULLs are distinct in a unique
-- index, and the presence claim would then never conflict with itself.
-- The rules live in services/dispatch_claims.py; this is only the record.

CREATE TABLE IF NOT EXISTS dispatch_claims (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  scope          TEXT        NOT NULL,
  drive_file_id  TEXT        NOT NULL,
  revision       TEXT        NOT NULL DEFAULT '',
  attempts       INTEGER     NOT NULL DEFAULT 1,
  claimed_at     TIMESTAMPTZ NOT NULL,
  -- The queue message the latest dispatch became; a repeat ask is answered
  -- with it. NULL between the claim and the enqueue.
  message_id     TEXT,
  capped_at      TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_dispatch_claims_key UNIQUE (scope, drive_file_id, revision)
);
