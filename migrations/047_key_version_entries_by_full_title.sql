-- Migration 047: key version catalog entries by their full title
--
-- The ingest now keeps a version in the catalog key (services/song_identity):
-- "Boyfriend (Acoustic)" is keyed "boyfriend (acoustic)", not "boyfriend".
-- Entries created before that were keyed by the shared normalizer, which
-- drops a trailing "(Acoustic)" / "(Remix)" and everything after "feat.", so
-- they share the plain song's key. Left that way, the next play of the
-- version would not find its entry, and a plain play could land in it.
--
-- Re-keys those entries to their lowercased title, the key the ingest now
-- computes for them. Only titles the normalizer cut a version from are
-- touched: a trailing "(Acoustic)" or "(Remix)", or a remix / acoustic /
-- live / stripped / session bracket next to a feat. credit. Release labels
-- ("(Original Mix)", "(Radio Edit)", "(Clean)") are not versions and keep
-- their key. Where two entries of one owner and artist would get the same
-- key, only the oldest is re-keyed; the other is left as it is.
--
-- Entries already keyed this way (most, from migrations 039–046) are
-- unchanged, and a re-run changes nothing.

WITH candidates AS (
  SELECT
    c.id, c.owner_id, c.artist_normalized, lower(c.title) AS new_key,
    row_number() OVER (
      PARTITION BY c.owner_id, lower(c.title), c.artist_normalized
      ORDER BY c.created_at, c.id
    ) AS rn
  FROM track_catalog c
  WHERE c.title_normalized <> lower(c.title)
    AND (
      c.title ~* '\((acoustic|remix)\)\s*$'
      OR c.title ~* '(feat\.|ft\.|featuring)\s.*[\[(][^\])]*(remix|acoustic|live|stripped|session)[^\])]*[\])]'
      OR c.title ~* '[\[(][^\])]*(remix|acoustic|live|stripped|session)[^\])]*\s(feat\.|ft\.|featuring)\s'
    )
)
UPDATE track_catalog c
SET title_normalized = k.new_key
FROM candidates k
WHERE c.id = k.id
  AND k.rn = 1
  AND NOT EXISTS (
    SELECT 1 FROM track_catalog x
    WHERE x.owner_id = k.owner_id
      AND x.title_normalized = k.new_key
      AND x.artist_normalized = k.artist_normalized
  );
