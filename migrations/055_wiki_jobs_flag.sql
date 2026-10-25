-- Migration 055: the switch for wiki jobs
--
-- When a source is composed, the API can ask wiki-curator-cog to link it,
-- and that job's follow-ons regenerate the pages it touched (wiki-curator-cog
-- ADR-0006). Every job costs model calls, so the hook is off until
-- switched on: with this FALSE a composed source is simply not linked yet,
-- and the curator's local commands pick it up whenever they next run.
--
-- flags.wcs.wiki_jobs_enabled
--   TRUE  → POST /v1/wcs/sources enqueues a wiki link job for the source
--   FALSE → it does not (the seeded state)
--
-- Seeded rather than left absent: an absent flag reads as enabled.

INSERT INTO feature_flags (id, owner_id, name, enabled, description)
VALUES (
  gen_random_uuid(),
  'kaiano',
  'flags.wcs.wiki_jobs_enabled',
  FALSE,
  'Send each composed WCS source to wiki-curator-cog to be linked'
)
ON CONFLICT (name) DO NOTHING;
