-- Migration 065: signal streaks
--
-- The fleet detects its own problems: a failed CI run, a cog run that
-- crashed, a conformance finding, a burst of 5xx. Most heal on their own (a
-- re-run once a release has reached PyPI, the next run of a flaky flow), and
-- a task for each would bury the board. A streak is one problem from its
-- first failure until it recovers, and it reaches the board only when it
-- escalates under its kind's rule. The rules live in services/signals.py;
-- this is only the record.
--
-- One open streak per (kind, key): the partial unique index is what turns a
-- second failure into a bump of the first rather than a second streak. A
-- closed streak stays as history, and the next failure opens a new one.

CREATE TABLE IF NOT EXISTS signal_streaks (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  kind              TEXT        NOT NULL,
  key               TEXT        NOT NULL,
  title             TEXT        NOT NULL,
  -- The latest failure's link and details, so the task names the newest run.
  link              TEXT,
  details           TEXT,
  failures          INTEGER     NOT NULL DEFAULT 1,
  remediations      INTEGER     NOT NULL DEFAULT 0,
  opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Set once the streak has been handed to task-manager-cog; cleared again
  -- if that request did not land, so the next failure tries again.
  escalated_at      TIMESTAMPTZ,
  task_external_id  TEXT,
  closed_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_signal_streaks_open
  ON signal_streaks (kind, key)
  WHERE closed_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_signal_streaks_kind_key
  ON signal_streaks (kind, key, opened_at DESC);
