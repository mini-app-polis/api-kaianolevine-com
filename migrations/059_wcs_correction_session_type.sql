-- Migration 059: authored notes are stored as session type 'correction'
--
-- An authored note (a Drive file named "<date> <instructor> > Note - <title>",
-- or "> Correction - <title>") goes through the same pipeline as a lesson.
-- transcription-cog used to store it as session type 'other', the same as
-- any unclassified session, so after ingest it could not be told apart from
-- one, and the site showed it as an "Other" lesson. It now sends
-- 'correction', and the API serves that type.
--
-- This moves any note already stored as 'other' over. Probably none: the
-- first real note had not been ingested when this was written. Notes are
-- recognised by their transcript's filename, using the same rule as
-- transcription-cog's filename parser: the right-hand side of " > " is the
-- word note or correction, singular or plural, alone, optionally followed by
-- " - <title>". A source whose session type an admin corrected is left
-- alone, since only 'other' rows are touched.
--
-- session_type is free text (no CHECK constraint), so nothing else changes.

UPDATE wcs_sources AS s
SET session_type = 'correction'
FROM wcs_transcripts AS t
WHERE s.transcript_id = t.id
  AND s.session_type = 'other'
  AND t.source_filename ~* '>\s*(note|notes|correction|corrections)\s*(\s-\s.*)?(\.[^.]+)?$';
