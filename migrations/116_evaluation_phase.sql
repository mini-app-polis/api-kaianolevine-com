-- Migration 116: record which release phase each finding was graded in
--
-- The release path evaluates a repository twice: the commit about to be
-- released (pre-release, before any tag exists) and the release just
-- published (post-release). repo_commit and repo_version (migration 092)
-- say what was graded; they cannot say which of the two evaluations a row
-- came from, and a pre-release row has no tag to tell it apart.
--
-- Nullable: rows that predate this have no answer, and an evaluation asked
-- for outside the release path has no phase.

ALTER TABLE pipeline_evaluations
  ADD COLUMN IF NOT EXISTS phase TEXT;
