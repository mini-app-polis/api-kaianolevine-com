-- Migration 076: rotations recorded with no automatic check
--
-- rotate.py writes a manual secret's new value through the rotation process,
-- and most manual secrets have no automatic check. Such a rotation is
-- recorded as 'unchecked': it counts as rotated, not as failed, and stays
-- distinguishable from one a check proved. A broken value shows up in the
-- failures of whatever uses it. Only a failed rotation carries a reason, as
-- rotation_events_reason_only_when_failed (migration 072) already requires.

ALTER TABLE rotation_events DROP CONSTRAINT IF EXISTS rotation_events_verification_check;
ALTER TABLE rotation_events ADD CONSTRAINT rotation_events_verification_check
  CHECK (verification IN ('ok', 'failed', 'unchecked'));
