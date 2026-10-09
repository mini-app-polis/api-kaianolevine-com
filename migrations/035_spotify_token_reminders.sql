-- Migration 035: Spotify token reminders
--
-- deejay-cog's Spotify refresh token expires six months after sign-in
-- (enforced by Spotify from 2026-07-20; refreshing does not extend it), and
-- only a person approving the app in a browser can renew it. This service
-- checks the token's age on a timer and posts a reminder with a one-click
-- re-auth link at 30, 14, 7 and 1 days before expiry, and once after.
--
-- The timer runs every hour and in every process, so this table is what
-- makes each reminder go once: one row per (token issue date, days before
-- expiry), written before the message is sent and removed again if Discord
-- refuses it. A renewed token has a new issue date and starts afresh. The
-- rules live in services/spotify_token.py; this is only the record.

CREATE TABLE IF NOT EXISTS spotify_token_reminders (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  issued_on    DATE        NOT NULL,
  days_before  INTEGER     NOT NULL,
  sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_spotify_token_reminders_key UNIQUE (issued_on, days_before)
);
