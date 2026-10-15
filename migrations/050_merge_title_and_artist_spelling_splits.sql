-- Migration 050: merge eight songs held under two spellings
--
-- Approved 2026-10-15, each one recording held twice because the title or
-- the artist was spelled two ways:
--   * Space Ghost Coast To Coast / Space Ghost (Coast To Coast) (Glass
--     Animals). The Stripped Back version stays separate.
--   * Tell Me: Bobby Valentino / Bobby V.
--   * Bonbon: Era Istefi / Era Istrefi. The Post Malone Remix stays separate.
--   * XO: EDEN / The Eden Project.
--   * Youngblood: 5 Seconds Of Summer / Seconds Of Summer.
--   * Church On Sunday: SAYGRACE / Grace.
--   * Thong Song: Amber Mack / Amber Mark.
--   * Stray Cat Strut: Peppy Polk / The Groove Factory / The Groove Factory
--     - Peppy Polk?
-- The correct spelling is kept where the other is a typo; otherwise the more
-- played entry's. Folded the same way as migration 037: plays move to the
-- kept entry, which keeps the first genre, BPM and release year any of them
-- had, its play count and first/last played are recomputed, and the other
-- entries are deleted. Play rows are untouched apart from catalog_id.
--
-- Keyed on production ids: a database without them changes nothing, and
-- once applied the folded ids are gone, so a re-run changes nothing.

CREATE TEMP TABLE catalog_merge (grp int, catalog_id uuid, canonical boolean) ON COMMIT DROP;

INSERT INTO catalog_merge (grp, catalog_id, canonical) VALUES
  -- 1. Space Ghost Coast To Coast — Glass Animals
  (1, '9261c4c3-91f2-4895-bd28-d53dee2445f4', true),
  (1, '5f4958b0-b88c-4cbc-b1c6-30e8c9fa3eb4', false),
  -- 2. Tell Me — Bobby Valentino
  (2, '58853861-266a-44fe-a20f-fa1f261259ce', true),
  (2, '37cadcac-39cf-4e57-a1c1-6e138bf24b59', false),
  -- 3. Bonbon — Era Istrefi
  (3, '3acaf566-d439-4191-bd00-1e020cc1f1cc', true),
  (3, '86659ae5-62a2-44f9-854e-4a2e23acbf2e', false),
  -- 4. XO — EDEN
  (4, 'ae9257a9-2dbd-4d3e-9bf8-5b044350a556', true),
  (4, '6e00b4b9-fc7c-4c90-abfb-1a0e76ecc09e', false),
  -- 5. Youngblood — 5 Seconds Of Summer
  (5, 'df6bfbf0-1826-4441-96c0-e5114724ebd5', true),
  (5, '9e26372a-f550-4bf4-8b59-4414310d4ba5', false),
  -- 6. Church On Sunday — SAYGRACE
  (6, '170af606-3ecf-4248-bf65-7c85d2ec960f', true),
  (6, '23fabb6d-6d6d-4601-b2fd-fdf331b89c49', false),
  -- 7. Thong Song — Amber Mack
  (7, '8903a088-45e7-4c58-b861-ff64f42248a2', true),
  (7, 'b1f23391-f925-4c76-951e-fef93b821b31', false),
  -- 8. Stray Cat Strut — Peppy Polk
  (8, 'cf0d88bf-a160-4c79-832d-2a98a4e421b8', true),
  (8, '1add69d8-1a0b-4b34-aedc-da08ded11590', false),
  (8, '582acf75-36d1-419b-8ee5-2752ad6dfa9c', false);

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
