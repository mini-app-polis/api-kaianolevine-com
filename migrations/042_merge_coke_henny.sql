-- Migration 042: count Coke Henny Pt 2 as Coke & Henny Pt. 2
--
-- One 2025 play spelled the song without its punctuation ("Coke Henny Pt 2
-- — Pink Sweat"), which put it under its own catalog entry beside "Coke &
-- Henny Pt. 2 — Pink Sweat$" (29 plays). Approved 2026-10-15. Folds the
-- stray entry in the same way as migration 037: plays move, empty metadata
-- fills, the folded entry is deleted, and play count and first/last played
-- are recomputed from the plays.
--
-- Keyed on production ids: a database without them changes nothing, and
-- once applied the folded id is gone, so a re-run changes nothing.

CREATE TEMP TABLE catalog_merge (grp int, catalog_id uuid, canonical boolean) ON COMMIT DROP;

INSERT INTO catalog_merge (grp, catalog_id, canonical) VALUES
  -- Coke & Henny Pt. 2 — Pink Sweat$
  (1, 'a652ac3b-0588-4003-a1ec-329df7ff5c6d', true),
  (1, 'cef8414a-4d63-4fff-892c-46df14a20cd5', false);

-- The canonical entry of each group that exists in this database.
CREATE TEMP TABLE catalog_merge_canon ON COMMIT DROP AS
SELECT m.grp, m.catalog_id AS canon_id
FROM catalog_merge m
JOIN track_catalog c ON c.id = m.catalog_id
WHERE m.canonical;

-- Every entry folding into a canonical one: each listed entry plus any
-- entry with the same normalized title and artist under another owner.
CREATE TEMP TABLE catalog_merge_moves ON COMMIT DROP AS
SELECT DISTINCT k.canon_id, c.id AS old_id
FROM catalog_merge m
JOIN catalog_merge_canon k ON k.grp = m.grp
JOIN track_catalog listed ON listed.id = m.catalog_id
JOIN track_catalog c
  ON c.title_normalized = listed.title_normalized
 AND c.artist_normalized = listed.artist_normalized
WHERE c.id <> k.canon_id;

-- Fill the canonical entry's empty metadata from the entries folding in,
-- most played first.
UPDATE track_catalog c
SET genre        = COALESCE(c.genre, f.genre),
    bpm          = COALESCE(c.bpm, f.bpm),
    release_year = COALESCE(c.release_year, f.release_year)
FROM (
  SELECT mv.canon_id,
         (array_agg(o.genre ORDER BY o.play_count DESC) FILTER (WHERE o.genre IS NOT NULL))[1] AS genre,
         (array_agg(o.bpm ORDER BY o.play_count DESC) FILTER (WHERE o.bpm IS NOT NULL))[1] AS bpm,
         (array_agg(o.release_year ORDER BY o.play_count DESC) FILTER (WHERE o.release_year IS NOT NULL))[1] AS release_year
  FROM catalog_merge_moves mv
  JOIN track_catalog o ON o.id = mv.old_id
  GROUP BY mv.canon_id
) f
WHERE c.id = f.canon_id;

UPDATE tracks t
SET catalog_id = mv.canon_id
FROM catalog_merge_moves mv
WHERE t.catalog_id = mv.old_id;

DELETE FROM track_catalog
WHERE id IN (SELECT old_id FROM catalog_merge_moves);

-- Play count and first/last played, recomputed from the plays now linked.
UPDATE track_catalog c
SET play_count   = p.plays,
    first_played = p.first_played,
    last_played  = p.last_played
FROM (
  SELECT t.catalog_id, count(*) AS plays,
         min(s.set_date) AS first_played, max(s.set_date) AS last_played
  FROM tracks t
  JOIN sets s ON s.id = t.set_id
  WHERE t.catalog_id IN (SELECT canon_id FROM catalog_merge_canon)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;
