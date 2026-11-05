-- Migration 092: record which version of the application each finding graded
--
-- A finding already names what graded it: standards_version (the catalog)
-- and evaluator_version (migration 031). It did not name what was graded,
-- so an Issue could say neither which release was found failing nor which
-- release came back clean. repo_version is the ref the evaluator read (a
-- release tag on a release-triggered evaluation) and repo_commit its sha.
--
-- Nullable: rows that predate this have no answer, and self-reported
-- findings from pipeline cogs evaluate no repository.

ALTER TABLE pipeline_evaluations
  ADD COLUMN IF NOT EXISTS repo_version TEXT,
  ADD COLUMN IF NOT EXISTS repo_commit TEXT;
