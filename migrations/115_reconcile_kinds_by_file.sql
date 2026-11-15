-- Migration 115: reconcile approvals of one file's kind
--
-- A kind approved for the catalog or for the file covers every file of
-- that kind. These columns hold one file's differences of a kind approved
-- on their own, in either direction: Drive file id to its kinds. For that
-- file the reconcile's write runs apply this approval ahead of the kind's,
-- so one file can go the other way from the rest of its kind (deejay-cog's
-- library reconcile, ADR-0008).
--
-- Empty by default, so the approvals already set keep their meaning.

ALTER TABLE library_reconcile_approvals
  ADD COLUMN IF NOT EXISTS kinds_by_file JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS file_wins_kinds_by_file JSONB NOT NULL DEFAULT '{}'::jsonb;
