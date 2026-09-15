-- Server-side idempotency for evaluation findings (PIPE-002).
--
-- SQS is at-least-once and the shared release workflow already retries the
-- evaluation POST five times, so the same finding will be offered twice.
-- Client-side deduplication in the evaluator compares against the single
-- most recent stored row and cannot catch either case.
--
-- The deferral this closes cited the complexity of constraining TEXT
-- columns. The text is not indexed here; its digest is.
--
-- `fingerprint` must agree exactly with
-- services/evaluation_fingerprint.py: SHA-256 over
-- (violation_id, dimension, severity, finding, suggestion), NULLs as empty
-- strings, joined by U+001F. A test pins the two to the same digest.
--
-- Not a generated column: `convert_to` is STABLE, so Postgres rejects this
-- expression in a generation expression, and the alternatives either need
-- an extension or a weaker hash. The API computes it on write instead, and
-- the index below is what actually enforces the guarantee.

ALTER TABLE pipeline_evaluations
  ADD COLUMN IF NOT EXISTS fingerprint TEXT;

UPDATE pipeline_evaluations
   SET fingerprint = encode(
         sha256(
           convert_to(
             coalesce(violation_id, '') || E'\x1F' ||
             coalesce(dimension,    '') || E'\x1F' ||
             coalesce(severity,     '') || E'\x1F' ||
             coalesce(finding,      '') || E'\x1F' ||
             coalesce(suggestion,   ''),
             'UTF8'
           )
         ), 'hex')
 WHERE fingerprint IS NULL;

ALTER TABLE pipeline_evaluations
  ALTER COLUMN fingerprint SET NOT NULL;

-- Existing duplicates, which must go before the index will build. This is
-- the actual work in this migration and it is unrelated to the queue.
--
-- The survivor is the earliest row of each group, ordered the way the read
-- path orders rows (COALESCE(evaluated_at, created_at), because migration
-- 002 added evaluated_at nullable and pre-002 rows still carry NULL), with
-- id as a deterministic tie-breaker. Keeping the earliest rather than the
-- latest means the surviving row is the one already referenced by whatever
-- has read this table since.
DELETE FROM pipeline_evaluations
 WHERE id IN (
   SELECT id
     FROM (
       SELECT id,
              row_number() OVER (
                PARTITION BY run_id, repo, fingerprint
                ORDER BY coalesce(evaluated_at, created_at) ASC, id ASC
              ) AS rn
         FROM pipeline_evaluations
        WHERE run_id IS NOT NULL
     ) ranked
    WHERE ranked.rn > 1
 );

-- Partial, because run_id is nullable and rows without one are not part of
-- any run: they have nothing to be idempotent with respect to, and making
-- them unique on (repo, fingerprint) alone would reject a repository
-- legitimately reporting the same thing on two different occasions.
CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_evaluations_run_repo_fingerprint
    ON pipeline_evaluations (run_id, repo, fingerprint)
 WHERE run_id IS NOT NULL;
