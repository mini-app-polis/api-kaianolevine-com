-- Migration 049: merge five songs held twice under two spellings
--
-- Approved 2026-10-15: Bodie / Bodies (Bryce Fox), 1000 X's & 0's / O's
-- (Prince), Miss Yo / Miss You (Aslove Remix) (Carla Bruni), The Simple
-- Things / Simple Things (Michael Carreon), Can I Rock It / Can I Rock
-- (Cunnie Williams). The correct spelling is kept where the other is a
-- typo; otherwise the more played entry's. Folded the same way as migration
-- 037: plays move to the kept entry, which keeps the first genre, BPM and
-- release year any of them had, its play count and first/last played are
-- recomputed, and the other entry is deleted. Play rows are untouched apart
-- from catalog_id.
--
-- Keyed on production ids: a database without them changes nothing, and
-- once applied the folded ids are gone, so a re-run changes nothing.

CREATE TEMP TABLE catalog_merge (grp int, catalog_id uuid, canonical boolean) ON COMMIT DROP;

INSERT INTO catalog_merge (grp, catalog_id, canonical) VALUES
  -- 1. Bodie — Bryce Fox
  (1, 'ac57eee9-03be-4d26-a43a-c7c0f64b25cd', true),
  (1, 'd33589ac-be05-4e26-ad25-d3bd18e80dab', false),
  -- 2. 1000 X's & O's — Prince
  (2, 'd4c8b18a-f50d-4a05-9308-4e4419dd7634', true),
  (2, 'f4700ee5-0472-4ae2-8b45-0fddc5a1802e', false),
  -- 3. Miss You (Aslove Remix) — Carla Bruni
  (3, '413b5d5a-1d61-4fef-b0b8-0aa7a9d16b0a', true),
  (3, '62bbfae4-f0a6-4c48-a595-0c4cdacbb29b', false),
  -- 4. The Simple Things — Michael Carreon
  (4, '7cb92adc-ccb6-46b9-9130-45be138fe6ae', true),
  (4, '8b7243ef-4b47-4a52-b55c-81d136909d21', false),
  -- 5. Can I Rock It — Cunnie Williams
  (5, '230ebe31-0c05-4fc7-8729-e97d7e01bd52', true),
  (5, '547e51d8-b669-49ff-9cab-1d8526988d15', false);

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
