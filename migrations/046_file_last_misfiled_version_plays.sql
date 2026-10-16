-- Migration 046: file the last 42 misfiled version plays
--
-- A full review on 2026-10-15 of all 14,681 plays (2016–2026) against
-- their catalog entries found these still under the wrong version of a song.
-- Approved play by play; each play has an explicit target here.
--
--   Raindrops [intl. Version] x19 (2024–26)    -> Raindrops (intl. Version)
--   Starboy ft. Daft Punk (Alex Aiono Cover) x6 (2017)
--                                              -> new "Starboy (Alex Aiono Cover)"
--   Shape Of You (Acoustic) x3 (2017)          -> Shape Of You (Acoustic)
--   Lost In Japan (Remix) x3 (2019–21)         -> Lost In Japan (Remix)
--   Drift Away x2 (2021), plain                -> new plain "Drift Away"
--   Boyfriend (Acoustic) (2016)                -> Boyfriend (Acoustic)
--   Dancing With A Stranger (Acoustic) (2019)  -> Dancing With A Stranger (Acoustic)
--   Damn (Acoustic) (2021)                     -> new "Damn (Acoustic)"
--   Flat Champagne (feat. RAY BLK) [Acoustic] (2021)
--                                              -> new "... (Acoustic)"
--   Head Underwater (Acoustic) (2026)          -> new "Head Underwater (Acoustic)"
--   So Into You [Darkchild Main Version], Floating [Filous Remix],
--   Unstoppable [FKJ Remix], The Weight Is Gone [The Purgatory Sessions]
--   (2026, one each)                           -> their version entries
--
-- Left alone on purpose: DJ Don't (Remix) (merged with DJ Don't), Black
-- Coffee In Bed (confirmed acoustic), Things Are Changin' [Acoustic Live]
-- (same version), Love Nwantiti [Violin] (the artist is the violin version).
--
-- New entries are keyed on their full lowercased title, so the stats (which
-- group songs by normalized title and artist) keep them apart from the plain
-- song; "Drift Away (Remix)" is re-keyed the same way first so the new plain
-- "Drift Away" does not fold back into it. Entries touched have play count
-- and first/last played recomputed; an entry left with no plays is deleted.
--
-- Keyed on production ids: a database without them changes nothing, and a
-- re-run finds the plays already filed and changes nothing.

UPDATE track_catalog
SET title_normalized = lower(title)
WHERE id = '4b9098a8-3598-4a50-b029-96e16febd9fa'
  AND title_normalized <> lower(title);

CREATE TEMP TABLE fix_046 (track_id uuid, to_id uuid, new_title text) ON COMMIT DROP;

INSERT INTO fix_046 (track_id, to_id, new_title) VALUES
  ('e946fd9a-7aae-4686-be23-747a65046a61', '7c2dc12f-4d88-4c82-a696-ed7a0f6e071c', NULL),
  ('4fcc4b3f-7379-4ac7-8179-b29c367dca6e', NULL, 'Starboy (Alex Aiono Cover)'),
  ('605cecab-51fa-484d-ae07-bae717b9b9cf', NULL, 'Starboy (Alex Aiono Cover)'),
  ('b3dd8272-d449-4e24-984f-7bc423fc082e', NULL, 'Starboy (Alex Aiono Cover)'),
  ('e6205a8c-61d7-4dd1-a90f-bc9cdb9b45fa', NULL, 'Starboy (Alex Aiono Cover)'),
  ('bd24f66c-7617-4202-94f4-039e33a3fe46', NULL, 'Starboy (Alex Aiono Cover)'),
  ('f45b2503-ec1f-495e-baf8-12222ca54cb6', NULL, 'Starboy (Alex Aiono Cover)'),
  ('c32a7659-2ff2-406a-b261-05cb8c273afc', '8b4fff3f-81f8-4912-9c4c-42c34fc6455e', NULL),
  ('a2fca67c-7159-4b03-8cab-9303ac8bb963', '8b4fff3f-81f8-4912-9c4c-42c34fc6455e', NULL),
  ('b7166b64-b4e9-474d-8f5d-dcf330d9a102', '8b4fff3f-81f8-4912-9c4c-42c34fc6455e', NULL),
  ('2c643f55-3ba1-4d97-ae86-2735a773aa06', '9d836529-a0e3-41b7-bd63-22c7bf6c7ae8', NULL),
  ('21836e4e-03a8-4cb5-b852-33e1191aa50b', '74036928-bdc8-4ad8-895a-326536a8b55e', NULL),
  ('6cfb2594-9263-48c9-98e9-5c94a16f7240', '74036928-bdc8-4ad8-895a-326536a8b55e', NULL),
  ('aa7d1237-91a4-4558-b69e-cbb2007efe0b', '74036928-bdc8-4ad8-895a-326536a8b55e', NULL),
  ('5dd9f17a-166f-4ddf-a346-89408ebbaa23', NULL, 'Damn (Acoustic)'),
  ('e0a6f007-fb5d-461b-b136-de6c5e8e60c5', NULL, 'Flat Champagne (feat. RAY BLK) (Acoustic)'),
  ('8b44a0c3-e7a1-4712-80c4-972861acf1fe', NULL, 'Drift Away'),
  ('0bc34ab8-1e26-49dd-83f1-075be8dc3f76', NULL, 'Drift Away'),
  ('b2f04eef-a60e-4049-9a40-af99fd05eadd', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('e393a031-1f4f-408e-af10-5dc0a8b5afd7', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('dac3122e-54a3-4898-be28-d1b5bbb7f6ee', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('7a07f8c6-b190-41c0-b56d-44967eaf110e', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('ce84cc0c-f2ea-480b-b3fa-486b59116cda', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('52975c88-06a9-4f2d-bf38-047517b05d7e', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('1fc6a87b-2083-49ad-979b-53a949ed2b8c', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('aa626e42-381f-462d-9352-953180a4321d', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('0dd19e07-4e7f-41ee-b828-fb1179560b4f', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('958e9a7a-6708-4b3a-a2ae-034a8dfd3639', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('ce1ae3f7-0bb6-4cef-927b-27774bd72c5c', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('b2eb91e9-f8c0-4521-96e4-ed647f48721a', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('9fae145f-7561-4159-9a8d-93fc7f48011f', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('6216805f-82d3-497e-b666-1fd19181c46d', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('5d445ef8-e030-49f0-8db3-f11c5bf92512', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('95ada618-4f3f-45f3-943f-73db001f1291', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('7c0ac49a-c57c-429e-8375-c4919ec2403e', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('fcfd657e-771d-4447-8d9b-d45238c71ecf', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('2d9a2fa4-278b-47f5-936e-4755fe3e488b', '72aa11c2-0be2-4e1c-81f0-d13539abf4d2', NULL),
  ('8c502574-5024-45b8-bfe5-1cf73527dc76', 'edf18518-d802-4f23-b6d1-44489585e2e1', NULL),
  ('1e9f00e6-335c-4020-96be-ee244fbeccd3', 'c66f1b39-427d-473d-bb45-ad2eaf54d478', NULL),
  ('5395dc3e-1ebb-4ebe-89bf-f38dd37424f6', 'ddd384dd-54f4-4bd7-bda7-77988b889d26', NULL),
  ('219e654e-ae91-462a-9f01-2eadca8177a7', 'e5c12e4b-e989-448c-977a-60d89cb26794', NULL),
  ('cf60ee5b-e020-45a0-bfaa-2f740dd005b5', NULL, 'Head Underwater (Acoustic)');

CREATE TEMP TABLE fix_046_rows ON COMMIT DROP AS
SELECT f.track_id, f.to_id, f.new_title, c.id AS from_id, c.owner_id, c.artist, c.artist_normalized
FROM fix_046 f
JOIN tracks t ON t.id = f.track_id
JOIN track_catalog c ON c.id = t.catalog_id;

-- New entries, one per title and artist, unless one already exists.
INSERT INTO track_catalog (
  id, owner_id, title, artist, title_normalized, artist_normalized,
  source, confidence, play_count
)
SELECT DISTINCT ON (lower(r.new_title), r.artist_normalized)
  gen_random_uuid(), r.owner_id, r.new_title, r.artist, lower(r.new_title), r.artist_normalized,
  'play_history', 'low', 0
FROM fix_046_rows r
WHERE r.new_title IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM track_catalog c
    WHERE c.title_normalized = lower(r.new_title) AND c.artist_normalized = r.artist_normalized
  )
ORDER BY lower(r.new_title), r.artist_normalized
ON CONFLICT (owner_id, title_normalized, artist_normalized) DO NOTHING;

UPDATE fix_046_rows r
SET to_id = (
  SELECT c.id FROM track_catalog c
  WHERE c.title_normalized = lower(r.new_title) AND c.artist_normalized = r.artist_normalized
  ORDER BY c.created_at, c.id LIMIT 1
)
WHERE r.new_title IS NOT NULL;

CREATE TEMP TABLE fix_046_moves ON COMMIT DROP AS
SELECT r.track_id, r.from_id, r.to_id
FROM fix_046_rows r
JOIN track_catalog target ON target.id = r.to_id
WHERE r.to_id <> r.from_id;

UPDATE tracks t
SET catalog_id = m.to_id
FROM fix_046_moves m
WHERE t.id = m.track_id;

UPDATE track_catalog c
SET genre        = COALESCE(c.genre, p.genre),
    bpm          = COALESCE(c.bpm, p.bpm),
    release_year = COALESCE(c.release_year, p.release_year)
FROM (
  SELECT t.catalog_id,
         (array_agg(t.genre) FILTER (WHERE t.genre IS NOT NULL))[1] AS genre,
         (array_agg(t.bpm) FILTER (WHERE t.bpm IS NOT NULL))[1] AS bpm,
         (array_agg(t.release_year) FILTER (WHERE t.release_year IS NOT NULL))[1] AS release_year
  FROM tracks t
  WHERE t.catalog_id IN (SELECT to_id FROM fix_046_moves)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;

UPDATE track_catalog c
SET play_count   = p.plays,
    first_played = p.first_played,
    last_played  = p.last_played
FROM (
  SELECT t.catalog_id, count(*) AS plays,
         min(s.set_date) AS first_played, max(s.set_date) AS last_played
  FROM tracks t
  JOIN sets s ON s.id = t.set_id
  WHERE t.catalog_id IN (SELECT to_id FROM fix_046_moves UNION SELECT from_id FROM fix_046_moves)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;

DELETE FROM track_catalog c
WHERE c.id IN (SELECT from_id FROM fix_046_moves)
  AND NOT EXISTS (SELECT 1 FROM tracks t WHERE t.catalog_id = c.id);
