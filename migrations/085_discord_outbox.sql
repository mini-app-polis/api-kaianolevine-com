-- Migration 085: the Discord outbox
--
-- A message the notification routes could not send straight away (no send
-- budget token free, or a rate limit holding the webhook) is kept here, and
-- the outbox drainer in this service (services.discord_outbox) sends it
-- when the channel is free. Rows are leased with FOR UPDATE SKIP LOCKED, so
-- a restart loses nothing and more than one process can drain.
--
-- kind
--   message  a Discord message body (payload), posted to the bare webhook URL
--   github   GitHub's payload as it arrived (raw_body), posted to /github
--   expired  the one summary of messages that expired unsent (payload holds
--            the count and the time range), posted to runs
--
-- never_expires is set for messages at WARN, ERROR or CRITICAL and anything
-- for the errors channel; every other row is deleted unsent once it is
-- older than DISCORD_OUTBOX_EXPIRE_HOURS, and only counted. attempts counts
-- sends of the row that the transport did not confirm with no sign of a
-- rate limit; a row with attempts is sent on its own, and is given up after
-- a bounded number of them.

CREATE TABLE IF NOT EXISTS discord_outbox (
  id               BIGSERIAL    PRIMARY KEY,
  channel          TEXT         NOT NULL,
  kind             TEXT         NOT NULL CHECK (kind IN ('message', 'github', 'expired')),
  payload          JSONB,
  raw_body         BYTEA,
  github_event     TEXT,
  github_delivery  TEXT,
  severity         TEXT,
  caller           TEXT         NOT NULL,
  never_expires    BOOLEAN      NOT NULL DEFAULT false,
  attempts         INTEGER      NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CHECK ((kind = 'github') = (raw_body IS NOT NULL)),
  CHECK ((kind = 'github') = (payload IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_discord_outbox_channel_id
  ON discord_outbox (channel, id);
