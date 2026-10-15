-- Migration 036: restore plays dropped for a blank artist
--
-- The retired ingest client skipped any source row whose Artist cell was
-- empty (the API requires an artist, so it could not send them). Nine plays
-- in 2016, 2019 and 2021 were lost that way. The annual summary sheets
-- count them, and the original set files still hold them, each at the gap
-- in play_order where it was skipped (play_order = source row - 1).
--
-- The ingest pipeline no longer exists, so they are restored here by hand:
-- the source title and length as written, the artist filled in, and each
-- play linked to the catalog entry that song already has.
--
--   Wobble Baby                    -> V.I.C.         (catalog "Wobble")
--     2016-06-15 FiRE+iCE #17, 2016-07-06 FiRE+iCE #8, 2016-08-17 FiRE+iCE #22
--   Thinking About It (KVR Remix)  -> Nathan Goshen
--     2019-08-02 NEDF Friday Night #28, 2021-12-30 Countdown Swing Boston #51
--   XO                             -> EDEN
--     2021-07-31 #36, 2021-08-26 #13, 2021-09-02 Ballroom In Boston #40,
--     2021-09-18 Philly Swing Classic #70
--
-- Keyed on production ids, so it inserts nothing in a database without
-- those sets and catalog entries. A play already at its set and play_order
-- is left alone, so re-running changes nothing. The catalog's stored play
-- count and first/last played move with the rows actually inserted.

WITH patch (set_id, catalog_id, play_order, title, artist, length_secs) AS (
  VALUES
    ('be9d217f-0b50-4645-93cc-3431a1498013', '0562db2b-d204-4c57-a4c4-3fd862b826bb', 17, 'Wobble Baby', 'V.I.C.', NULL::int),
    ('c2e1ea36-969a-4a31-a0d8-bc15dfc09a37', '0562db2b-d204-4c57-a4c4-3fd862b826bb',  8, 'Wobble Baby', 'V.I.C.', NULL),
    ('66f17eea-de55-4639-80bf-1b6523743a45', '0562db2b-d204-4c57-a4c4-3fd862b826bb', 22, 'Wobble Baby', 'V.I.C.', NULL),
    ('119e7847-2db4-4d72-9c52-edb4a2f7bb96', 'a11b161b-29c9-4298-8d56-ba80521c0392', 28, 'Thinking About It (Let It go) (KVR Remix)', 'Nathan Goshen', 185),
    ('003a9a49-9380-4012-b866-4768e8435b81', 'a11b161b-29c9-4298-8d56-ba80521c0392', 51, 'Thinking About It (Let It go) (KVR Remix)', 'Nathan Goshen', 185),
    ('5d27ffa3-1875-4811-96a5-f17eb2af8eef', 'ae9257a9-2dbd-4d3e-9bf8-5b044350a556', 36, 'XO', 'EDEN', 162),
    ('2aba9bd5-0a15-4a6c-b757-5a2720da9848', 'ae9257a9-2dbd-4d3e-9bf8-5b044350a556', 13, 'XO', 'EDEN', 162),
    ('3ea18796-e5a3-4563-9602-e0c44ab8ef17', 'ae9257a9-2dbd-4d3e-9bf8-5b044350a556', 40, 'XO', 'EDEN', 162),
    ('2e1b447d-6177-4ef5-8ea9-3eacb61e486d', 'ae9257a9-2dbd-4d3e-9bf8-5b044350a556', 70, 'XO', 'EDEN', 162)
),
inserted AS (
  INSERT INTO tracks (
    owner_id, set_id, catalog_id, play_order, title, artist, length_secs, data_quality
  )
  SELECT
    s.owner_id, s.id, c.id, p.play_order, p.title, p.artist, p.length_secs,
    CASE WHEN p.length_secs IS NULL THEN 'minimal' ELSE 'partial' END
  FROM patch p
  JOIN sets s ON s.id = p.set_id::uuid
  JOIN track_catalog c ON c.id = p.catalog_id::uuid
  WHERE NOT EXISTS (
    SELECT 1 FROM tracks t WHERE t.set_id = s.id AND t.play_order = p.play_order
  )
  RETURNING catalog_id, set_id
),
per_catalog AS (
  SELECT i.catalog_id, count(*) AS plays,
         min(s.set_date) AS first_played, max(s.set_date) AS last_played
  FROM inserted i
  JOIN sets s ON s.id = i.set_id
  GROUP BY i.catalog_id
)
UPDATE track_catalog c
SET play_count   = c.play_count + pc.plays,
    first_played = LEAST(c.first_played, pc.first_played),
    last_played  = GREATEST(c.last_played, pc.last_played)
FROM per_catalog pc
WHERE c.id = pc.catalog_id;
