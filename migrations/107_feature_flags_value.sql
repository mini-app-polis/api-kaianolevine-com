-- Migration 107: an optional value on feature flags
--
-- A flag can carry a text value next to its on/off state (for example a mode
-- name). NULL for a plain on/off flag, so existing flags are unchanged.

ALTER TABLE feature_flags
  ADD COLUMN IF NOT EXISTS value TEXT;
