-- Migration 051: stop counting the "---SILENCE---" placeholder
--
-- "---SILENCE---" is a placeholder logged as played in a set, not a song.
-- The ingest now keeps such a row with no catalog entry, and the stats count
-- only plays linked to one (services/song_identity, routers/stats,
-- services/year_summaries). This unlinks the placeholder plays already
-- ingested and deletes their catalog entry. The play rows stay in their
-- sets.
--
-- A re-run finds nothing linked and no entry left, so it changes nothing.

UPDATE tracks
SET catalog_id = NULL
WHERE title ~* '^\s*-{2,}\s*silence\s*-{2,}\s*$';

DELETE FROM track_catalog c
WHERE c.title ~* '^\s*-{2,}\s*silence\s*-{2,}\s*$'
  AND NOT EXISTS (SELECT 1 FROM tracks t WHERE t.catalog_id = c.id);
