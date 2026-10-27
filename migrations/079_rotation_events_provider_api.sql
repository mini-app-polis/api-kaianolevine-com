-- Migration 079: rotations the rotator mints through a provider's API
--
-- A provider-api secret is minted by the provider, through its API, by
-- rotator-cog itself, where a manual one is minted in the provider's
-- console by a person (the first is TURNSTILE_SECRET_KEY, through
-- Cloudflare's rotate-secret API). The contract's RotationKind names it,
-- so the table accepts it too.

ALTER TABLE rotation_events DROP CONSTRAINT IF EXISTS rotation_events_kind_check;
ALTER TABLE rotation_events ADD CONSTRAINT rotation_events_kind_check
  CHECK (kind IN ('machine-key', 'generated', 'aws-access-key', 'manual', 'provider-api'));
