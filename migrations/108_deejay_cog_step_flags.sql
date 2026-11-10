-- Migration 108: deejay-cog's step modes as feature flags
--
-- Each deejay-cog step reads its mode (off, dry-run or write) from one flag,
-- flags.deejay_cog.STEP, in place of its NEW_MUSIC_MODE / LIBRARY_*_MODE
-- setting, so a change takes effect on the next run with no settings sync.
-- The mode is the flag's value; enabled is TRUE unless the mode is off, so
-- the on/off state agrees with it.
--
-- Seeded with the modes prd had when the flags replaced the settings (an
-- unset setting was off). Created once: a mode changed since is not reset.

INSERT INTO feature_flags (id, owner_id, name, enabled, description, value)
VALUES
  (gen_random_uuid(), 'kaiano', 'flags.deejay_cog.library_scan', TRUE,
   'deejay-cog library-scan step mode: off, dry-run or write', 'write'),
  (gen_random_uuid(), 'kaiano', 'flags.deejay_cog.library_trash', FALSE,
   'deejay-cog library-trash step mode: off, dry-run or write', 'off'),
  (gen_random_uuid(), 'kaiano', 'flags.deejay_cog.library_identify', TRUE,
   'deejay-cog library-identify step mode: off, dry-run or write', 'write'),
  (gen_random_uuid(), 'kaiano', 'flags.deejay_cog.library_backfill', TRUE,
   'deejay-cog library-backfill step mode: off, dry-run or write', 'write'),
  (gen_random_uuid(), 'kaiano', 'flags.deejay_cog.library_sync', TRUE,
   'deejay-cog library-sync step mode: off, dry-run or write', 'write'),
  (gen_random_uuid(), 'kaiano', 'flags.deejay_cog.library_duplicates', TRUE,
   'deejay-cog library-duplicates step mode: off, dry-run or write', 'write'),
  (gen_random_uuid(), 'kaiano', 'flags.deejay_cog.library_reconcile', TRUE,
   'deejay-cog library-reconcile step mode: off, dry-run or write', 'write'),
  (gen_random_uuid(), 'kaiano', 'flags.deejay_cog.new_music', TRUE,
   'deejay-cog new-music intake mode: off, dry-run or write', 'write')
ON CONFLICT (name) DO NOTHING;
