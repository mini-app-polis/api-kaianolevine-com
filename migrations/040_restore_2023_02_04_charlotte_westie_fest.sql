-- Migration 040: restore the 2023-02-04 Charlotte Westie Fest set
--
-- The set file is in the originals folder (25 plays) but the set never
-- reached the API. The file is well formed (same layout as 2023-02-02,
-- which did ingest); one Comment cell holds stray double quotes
-- ("Explicit - Fucking" 2:05"), the likeliest thing to have tripped the
-- retired ingest. The ingest no longer exists, so the set is written here.
--
-- Stored the way the API stores the 2023-02-02 set: owner and venue from
-- that set, play_order 1..25 in file order, Play Time as the time of day,
-- Length as seconds, BPM, year, label, comment and data_quality as the
-- ingest computed them, and the Remix column recorded on the play.
--
-- Each play is filed under its catalog entry the same way as migration 039:
-- a version in the Remix column files the play under "Title (Version)"; a
-- release label (here "Cover - Mahogany Sessions") stays with the song.
-- Entries are matched on title (+ version) ignoring case and punctuation and
-- on the main artist, preferring the same artist spelling, then the most
-- played. 24 of the 25 songs are already in the catalog; "With You" by
-- Charles Diamond is created. Every entry touched has its play count and
-- first/last played recomputed from its plays.
--
-- A database without the 2023-02-02 set changes nothing, and once the set
-- exists a re-run changes nothing.

CREATE TEMP TABLE set_0204 (
  play_order int, play_time time, label text, title text, remix text, artist text,
  comment text, genre text, bpm double precision, release_year int, length_secs int,
  data_quality text, title_normalized text, artist_normalized text, remix_kind text
) ON COMMIT DROP;

INSERT INTO set_0204 VALUES
  (1, '04:17:00', NULL, 'Capricorn', 'Live Acoustic', 'Elderbrook', NULL, 'Acoustic Live', 104.0, 2019, 172, 'complete', 'capricorn', 'elderbrook', 'version'),
  (2, '04:20:00', NULL, 'Fair Play', NULL, 'Malaki, Lucy Mcwilliams', NULL, 'Rap & Hip-Hop', 88.0, 2020, 289, 'complete', 'fair play', 'malaki, lucy mcwilliams', NULL),
  (3, '04:25:00', NULL, 'Cruel', 'Piano Acoustic', 'Oli Fox', NULL, NULL, 96.0, 2018, 196, 'partial', 'cruel', 'oli fox', 'version'),
  (4, '04:28:00', NULL, 'California Girls', 'NoMBe vs Sonny Alven Remix', 'NoMBe and Sonny Alven', NULL, 'Dance & DJ', 98.0, 2016, 187, 'complete', 'california girls', 'nombe and sonny alven', 'version'),
  (5, '04:49:00', NULL, 'Pulling Me Under', NULL, 'Kevin Garrett', NULL, 'Pop', 90.0, 2017, 265, 'complete', 'pulling me under', 'kevin garrett', NULL),
  (6, '04:53:00', 'E', 'Redbone', 'Cover - Mahogany Sessions', 'Miller Blue', 'Explicit - ??', 'Pop', 93.3, 2019, 241, 'complete', 'redbone', 'miller blue', 'label'),
  (7, '04:57:00', NULL, 'Stand and Deliver', NULL, 'Patrick Droney', NULL, 'Rock', 89.0, 2019, 224, 'complete', 'stand and deliver', 'patrick droney', NULL),
  (8, '05:23:00', 'E', 'With You', NULL, 'Charles Diamond', 'Explicit - ??', 'Rap & Hip-Hop', 90.0, 2015, 195, 'complete', 'with you', 'charles diamond', NULL),
  (9, '05:26:00', NULL, 'Brooklyn', NULL, 'Patrick Droney', NULL, 'Rock', 100.0, 2019, 227, 'complete', 'brooklyn', 'patrick droney', NULL),
  (10, '05:29:00', NULL, 'Genie in a Bottle', NULL, 'Oshri', NULL, 'Pop', 93.5, 2019, 199, 'complete', 'genie in a bottle', 'oshri', NULL),
  (11, '05:33:00', NULL, 'Emiliana', 'Acoustic', 'CKay', NULL, 'International', 83.0, 2022, 200, 'complete', 'emiliana', 'ckay', 'version'),
  (12, '05:36:00', NULL, 'Coke & Henny Pt. 2', NULL, 'Pink Sweat$', NULL, 'R&B/Soul', 90.0, 2019, 157, 'complete', 'coke & henny pt. 2', 'pink sweat', NULL),
  (13, '06:00:00', NULL, 'Open Season', NULL, 'Josef Salvat', NULL, 'Pop', 90.0, 2014, 235, 'complete', 'open season', 'josef salvat', NULL),
  (14, '06:04:00', NULL, 'Skin', NULL, 'Lume', NULL, 'Soul', 85.0, 2018, 198, 'complete', 'skin', 'lume', NULL),
  (15, '06:07:00', NULL, 'What A Wonderful World', NULL, 'Sofi Tukker', NULL, 'Pop', 83.0, 2022, 175, 'complete', 'what a wonderful world', 'sofi tukker', NULL),
  (16, '06:10:00', 'E', 'Hazey', NULL, 'Glass Animals', 'Explicit - Fucking" 2:05"', 'Electronic', 90.0, 2014, 266, 'complete', 'hazey', 'glass animals', NULL),
  (17, '06:14:00', NULL, 'What If I Told You That I Love You', NULL, 'Ali Gatie', NULL, 'Pop', 85.0, 2020, 194, 'complete', 'what if i told you that i love you', 'ali gatie', NULL),
  (18, '06:17:00', NULL, 'I Wanna (One Dance) With Somebody', NULL, 'Chris Mann', NULL, 'Jazz', 99.0, 2017, 226, 'complete', 'i wanna (one dance) with somebody', 'chris mann', NULL),
  (19, '06:21:00', NULL, 'Drops (Feat Tom Bailey)', NULL, 'FKJ', NULL, 'Hip-Hop', 88.0, 2015, 241, 'complete', 'drops (feat tom bailey', 'fkj', NULL),
  (20, '06:25:00', NULL, 'Trumpets', NULL, 'Jason Derulo', NULL, 'R&B', 82.0, 2014, 217, 'complete', 'trumpets', 'jason derulo', NULL),
  (21, '06:28:00', NULL, 'Waited All Summer', NULL, 'Phlake', NULL, 'Pop', 96.0, 2018, 231, 'complete', 'waited all summer', 'phlake', NULL),
  (22, '06:32:00', NULL, 'Things Are Changin''', 'Acoustic Live', 'Gary Clark Jr.', NULL, NULL, 85.6, 2004, 271, 'partial', 'things are changin', 'gary clark jr', 'version'),
  (23, '06:37:00', NULL, 'California King', NULL, 'D.B. Ricapito', NULL, 'Pop', 86.0, 2018, 195, 'complete', 'california king', 'd.b. ricapito', NULL),
  (24, '06:40:00', NULL, 'Acoustic', NULL, 'Billy Raffoul', NULL, 'Rock', 76.0, 2018, 172, 'complete', 'acoustic', 'billy raffoul', NULL),
  (25, '06:42:00', NULL, 'A Simple Go', NULL, 'Mar Omin', NULL, 'R&B/Soul', 85.0, 2014, 227, 'complete', 'a simple go', 'mar omin', NULL);

INSERT INTO sets (id, owner_id, set_date, venue, source_file)
SELECT gen_random_uuid(), s.owner_id, DATE '2023-02-04', 'Charlotte Westie Fest',
       '2023-02-04 Charlotte Westie Fest'
FROM sets s
WHERE s.source_file = '2023-02-02 Charlotte Westie Fest'
  AND NOT EXISTS (
    SELECT 1 FROM sets x WHERE x.source_file = '2023-02-04 Charlotte Westie Fest'
  );

-- The plays still to write: only while the restored set has none.
CREATE TEMP TABLE set_0204_rows ON COMMIT DROP AS
SELECT d.*, s.id AS set_id, s.owner_id,
       CASE WHEN d.remix_kind = 'version' THEN d.title || ' (' || d.remix || ')' ELSE d.title END AS display
FROM set_0204 d
JOIN sets s ON s.source_file = '2023-02-04 Charlotte Westie Fest'
WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.set_id = s.id);

ALTER TABLE set_0204_rows
  ADD COLUMN title_key text, ADD COLUMN artist_key text, ADD COLUMN catalog_id uuid;

UPDATE set_0204_rows SET
  title_key = regexp_replace(regexp_replace(lower(display), '[^a-z0-9]+', '', 'g'), 'version$', ''),
  artist_key = regexp_replace(
    regexp_replace(lower(artist), '\s*(,|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''),
    '[^a-z0-9]+', '', 'g');

CREATE TEMP TABLE set_0204_catalog ON COMMIT DROP AS
SELECT c.id, c.artist_normalized, c.play_count, c.created_at,
  regexp_replace(regexp_replace(lower(c.title || coalesce(' ' || c.remix, '')), '[^a-z0-9]+', '', 'g'), 'version$', '') AS title_key,
  regexp_replace(
    regexp_replace(lower(c.artist), '\s*(,|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''),
    '[^a-z0-9]+', '', 'g') AS artist_key
FROM track_catalog c;

UPDATE set_0204_rows r
SET catalog_id = (
  SELECT k.id FROM set_0204_catalog k
  WHERE k.title_key = r.title_key AND k.artist_key = r.artist_key
  ORDER BY (k.artist_normalized = r.artist_normalized) DESC, k.play_count DESC, k.created_at, k.id
  LIMIT 1
);

-- Songs the catalog does not have yet.
WITH created AS (
  INSERT INTO track_catalog (
    id, owner_id, title, artist, title_normalized, artist_normalized,
    source, confidence, genre, bpm, release_year, play_count
  )
  SELECT DISTINCT ON (r.title_key, r.artist_key)
    gen_random_uuid(), r.owner_id, r.display, r.artist,
    CASE WHEN r.remix_kind = 'version' THEN lower(r.display) ELSE r.title_normalized END,
    r.artist_normalized, 'play_history', 'low', r.genre, r.bpm, r.release_year, 0
  FROM set_0204_rows r
  WHERE r.catalog_id IS NULL
  ORDER BY r.title_key, r.artist_key, r.play_order
  ON CONFLICT (owner_id, title_normalized, artist_normalized) DO NOTHING
  RETURNING id, title, artist
)
UPDATE set_0204_rows r
SET catalog_id = c.id
FROM created c
WHERE r.catalog_id IS NULL AND c.title = r.display AND c.artist = r.artist;

INSERT INTO tracks (
  id, owner_id, set_id, catalog_id, play_order, play_time, label, title, remix,
  artist, comment, genre, bpm, release_year, length_secs, data_quality
)
SELECT gen_random_uuid(), r.owner_id, r.set_id, r.catalog_id, r.play_order, r.play_time,
       r.label, r.title, r.remix, r.artist, r.comment, r.genre, r.bpm, r.release_year,
       r.length_secs, r.data_quality
FROM set_0204_rows r;

UPDATE track_catalog c
SET play_count   = p.plays,
    first_played = p.first_played,
    last_played  = p.last_played
FROM (
  SELECT t.catalog_id, count(*) AS plays,
         min(s.set_date) AS first_played, max(s.set_date) AS last_played
  FROM tracks t
  JOIN sets s ON s.id = t.set_id
  WHERE t.catalog_id IN (SELECT catalog_id FROM set_0204_rows)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;
