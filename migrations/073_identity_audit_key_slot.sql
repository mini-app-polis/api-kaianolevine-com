-- Migration 073: which of a machine's keys proved it
--
-- A machine may hold a previous key beside its current one while a rotation
-- overlaps, and either authenticates it. The previous key is retired once
-- nothing presents it any more, and this column is how that is known: each
-- audit event for a key-authenticated request records the slot that matched,
-- and the rows already carry the machine (subject) and the time
-- (occurred_at). NULL for every other credential, and for rows written
-- before this migration.
--
-- The index holds only the rows that matter to retirement: requests proved
-- by a previous key, which exist only while a rotation is in progress.

ALTER TABLE identity_audit_events
  ADD COLUMN IF NOT EXISTS key_slot TEXT
    CHECK (key_slot IN ('current', 'previous'));

CREATE INDEX IF NOT EXISTS idx_identity_audit_previous_key
  ON identity_audit_events (subject, occurred_at DESC)
  WHERE key_slot = 'previous';
