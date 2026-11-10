-- Migration 109: reconcile approvals for the file
--
-- An approval used to mean one thing: write the catalog's value over the
-- file. A difference where the file is right had no way to close, so it
-- stayed open on the review page. These columns hold approvals in the
-- other direction: the file's value goes up to the catalog entry and the
-- file is not written (deejay-cog's library reconcile). Whole kinds, and
-- 'other' differences by file and field, as for the catalog's direction.
--
-- Empty by default, so the approvals already set keep their meaning.

ALTER TABLE library_reconcile_approvals
  ADD COLUMN IF NOT EXISTS file_wins_kinds TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS file_wins_files JSONB NOT NULL DEFAULT '{}'::jsonb;
