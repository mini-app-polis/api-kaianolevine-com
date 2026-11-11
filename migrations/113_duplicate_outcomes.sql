-- Migration 113: a duplicate group's outcome per file, and groups kept together
--
-- The owner resolves a duplicate group file by file: keep it on the entry,
-- give it its own version entry, or send it to the trash (deejay-cog
-- ADR-0007, amended 2026-11-11: one entry may keep several files). A group
-- whose files all stay on the entry is kept together; like any resolved
-- group, a later report does not reopen it unless its set of files changes.
--
-- Additive: every existing group is not kept together, and its members have
-- no recorded outcome.

ALTER TABLE library_duplicate_groups
  ADD COLUMN IF NOT EXISTS kept_together BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE library_duplicate_members
  ADD COLUMN IF NOT EXISTS outcome TEXT
    CHECK (outcome IN ('keep', 'own_version', 'trash'));
