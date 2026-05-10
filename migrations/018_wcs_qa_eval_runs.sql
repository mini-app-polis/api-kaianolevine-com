-- Migration 018: WCS Q&A eval-run results
--
-- Append-only log of eval-harness runs. The harness loads a question set from
-- tests/evals/questions.yaml, hits POST /v1/wcs/ask for each question,
-- computes auto-metrics (source_recall, source_precision), calls the judge
-- model for a 1-5 score + reasoning, and inserts one row per (run_id, question_id).
--
-- manual_grade and manual_grade_notes are reserved for the v1.1 admin UI
-- (/notes/ask/eval); harness writes leave them NULL. The composite PK lets the
-- admin UI write back to the same row by (run_id, question_id) without
-- needing a surrogate id.
--
-- judge_prompt_sha records a hash of the judge prompt at run time so prompt
-- drift is detectable when comparing runs across time.

CREATE TABLE IF NOT EXISTS wcs_qa_eval_runs (
    run_id              UUID NOT NULL,
    question_id         TEXT NOT NULL,
    git_sha             TEXT NOT NULL,
    agent_answer        TEXT NOT NULL,
    cited_source_ids    JSONB NOT NULL,
    tool_trace          JSONB NOT NULL,
    source_recall       FLOAT,
    source_precision    FLOAT,
    judge_score         INT,
    judge_reasoning     TEXT,
    judge_model         TEXT NOT NULL,
    judge_prompt_sha    TEXT NOT NULL,
    manual_grade        INT,
    manual_grade_notes  TEXT,
    ran_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_wcs_qa_eval_runs_ran_at
    ON wcs_qa_eval_runs (ran_at DESC);
