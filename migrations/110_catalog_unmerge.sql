-- Migration 110: catalog_revisions gains the kind 'unmerge'
--
-- The unmerge command (kaianolevine_api.cleanup.unmerge) puts back one
-- entry that a merge archived: its values, its plays and its library
-- files. It writes a revision of kind 'unmerge' on the entry and on the
-- keeper it came back from, so the merge and its undoing both stay in the
-- history.

ALTER TABLE catalog_revisions DROP CONSTRAINT IF EXISTS catalog_revisions_kind_check;
ALTER TABLE catalog_revisions ADD CONSTRAINT catalog_revisions_kind_check
  CHECK (kind IN ('genre_map', 'split_parts', 'merge', 'edit', 'unmerge'));
