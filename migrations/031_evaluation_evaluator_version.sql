-- Migration 031: record which evaluator build wrote each finding
--
-- run_id cannot carry this. A fleet pass's run_id is minted by the API
-- before any job is queued and shared by every repository in the pass, so
-- the evaluator that later consumes those jobs has no way to add its own
-- version without splitting the pass into one run per repository. And
-- standards_version names the catalog, not the code that applied it — the
-- two ship on separate release trains, so a finding's wording can come
-- from an evaluator older than the catalog it cites.
--
-- Nullable: every row that predates this has no answer, and self-reported
-- findings from pipeline cogs are not written by the evaluator at all.

ALTER TABLE pipeline_evaluations
  ADD COLUMN IF NOT EXISTS evaluator_version TEXT;
