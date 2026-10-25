-- Migration 070: claims on grooming jobs
--
-- A queue can deliver the same grooming job more than once, and each
-- delivery that reaches the model is a paid run. task-groomer-cog claims a
-- job before its model call, keyed on the task and the fingerprint of the
-- content it was sent for, and grooms only if it won the claim. The primary
-- key is what makes the claim atomic: of two deliveries claiming at once,
-- exactly one inserts.
--
-- A run that fails gives its claim back (state 'released'), so the next send
-- of the same content can claim it again. A run that succeeds keeps it, so
-- unchanged content is not groomed twice.
--
-- tasks.groomings.write, held by task-groomer, covers claiming and releasing.

CREATE TABLE IF NOT EXISTS task_grooming_claims (
  task_gid     TEXT        NOT NULL,
  fingerprint  TEXT        NOT NULL,
  state        TEXT        NOT NULL DEFAULT 'claimed'
                 CHECK (state IN ('claimed', 'released')),
  claimed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  released_at  TIMESTAMPTZ,
  PRIMARY KEY (task_gid, fingerprint)
);
