-- Migration 048: merge songs split by "_" in the artist, and two versions
--
-- Set files from 2024 on export a list of artists with "_" where the
-- earlier files had ",": "Malaki_ Lucy Mcwilliams" for "Malaki, Lucy
-- Mcwilliams". The catalog keys on the artist, so each of these songs was
-- held twice. The ingest now reads "_" as "," (services/song_identity), and
-- this folds the existing pairs, the same way migration 037 did: plays move
-- to the "," entry, which keeps the first genre, BPM and release year any
-- of them had, its play count and first/last played are recomputed, and
-- the "_" entry is deleted. Play rows are untouched apart from catalog_id.
--
-- Also folded, approved 2026-10-15:
--   * South of the Border (Acoustic), with Camila Cabello in the artist,
--     into South of the Border (feat. Camila Cabello) (Acoustic).
--   * Things Are Changin' (Acoustic Live) into Things Are Changin'
--     (Acoustic): the same recording.
--
-- Then the "_" entries with no "," twin are re-keyed to the "," spelling,
-- so their next "," play finds them. A "_" between digits (a routine file
-- name such as "115_116") is left alone.
--
-- Keyed on production ids: a database without them changes nothing, and
-- once applied the folded ids are gone and no "_" artist is left, so a
-- re-run changes nothing.

CREATE TEMP TABLE catalog_merge (grp int, catalog_id uuid, canonical boolean) ON COMMIT DROP;

INSERT INTO catalog_merge (grp, catalog_id, canonical) VALUES
  -- 1. Tainted Love — Trinix, Queen D
  (1, '5e317019-e126-4175-9e87-c74e863bec77', true),
  (1, 'f50ef9c9-c854-42c9-8749-f73fe21cbae7', false),
  -- 2. Fair Play — Malaki, Lucy Mcwilliams
  (2, '5b97e985-c05c-4695-87ce-605ca320c36c', true),
  (2, 'ebd8b4d4-9812-4631-b997-c08e8b1d2ee5', false),
  -- 3. Aliens Need Love Too — Phlake, Mercedes the Virus
  (3, '4676f2d2-58b8-48f7-8656-8246d2d7ae33', true),
  (3, '92584765-bc43-4054-8929-d2a618316295', false),
  -- 4. everything i'm missing — clear eyes, Marian Hill
  (4, 'd6d7744b-4952-48a9-88a3-19e30edb963c', true),
  (4, '055b62e7-4f10-4e88-87b9-ede2e5801641', false),
  -- 5. 3 to 1 — Monoir, Eneli
  (5, '6c430fbb-3bb8-4689-9b82-ad021cab926d', true),
  (5, '7cc4ad51-23ca-4773-8820-3782325743a1', false),
  -- 6. Hearts A Mess — JPL, A Girl Named Sue
  (6, '54f73ded-94dc-4ee9-a75a-77be967cae6c', true),
  (6, 'a0a637dc-0214-4bee-a460-470ff7f74d64', false),
  -- 7. Stubborn — Daniel Allan, Jake Neumar
  (7, '20479a15-08fb-4bac-b521-0bdc04782eb0', true),
  (7, 'e24aa7b6-a72c-4045-b25c-9d7c1be24765', false),
  -- 8. Lady Marmalade — Christina Aguilera, Lil' Kim, Mya And Pink
  (8, '88a8da68-9d7c-40cb-80c9-cd9986558976', true),
  (8, '47877512-a01e-49ae-ba86-77ab2b927233', false),
  -- 9. Give a Little — Ash, Naila
  (9, 'ffcd6fe0-92a1-4d25-9926-247954d4b0bc', true),
  (9, '1e13ebd8-1d20-4646-bdf3-f997da34451b', false),
  -- 10. OH NO! — Cody Lovaas, ROZES
  (10, '2b38ef4e-2c10-4724-aa3f-5978a1c59c47', true),
  (10, 'dac7802a-25c4-4cf3-8597-ce0fc0558f1e', false),
  -- 11. If You're Hearing This — Hook N Sling
  (11, '29575465-65f1-4cd7-8148-331df532aae8', true),
  (11, '1a060a0a-20c8-4ef3-a803-2425b92baf0b', false),
  -- 12. South of the Border (feat. Camila Cabello) (Acoustic) — Ed Sheeran
  (12, '4b14002a-74d7-4997-a379-90566ce21972', true),
  (12, '7f59cdf3-9379-4cae-8ab2-fb0912385f49', false),
  -- 13. Things Are Changin' (Acoustic) — Gary Clark Jr.
  (13, '2e427679-9087-4064-b0cc-dedb7796b26e', true),
  (13, 'a2a2511f-746c-432c-be3e-e488f75afe36', false);

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

-- The "_" entries left: re-key to the "," spelling, unless that key is
-- already taken for the owner.
UPDATE track_catalog c
SET artist            = regexp_replace(c.artist, '(?<![0-9])\s*_\s*(?![0-9])', ', ', 'g'),
    artist_normalized = regexp_replace(c.artist_normalized, '(?<![0-9])\s*_\s*(?![0-9])', ', ', 'g')
WHERE c.artist_normalized ~ '(?<![0-9])\s*_\s*(?![0-9])'
  AND NOT EXISTS (
    SELECT 1 FROM track_catalog x
    WHERE x.owner_id = c.owner_id
      AND x.title_normalized = c.title_normalized
      AND x.artist_normalized = regexp_replace(c.artist_normalized, '(?<![0-9])\s*_\s*(?![0-9])', ', ', 'g')
  );
