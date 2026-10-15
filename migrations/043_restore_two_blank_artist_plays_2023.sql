-- Migration 043: restore two 2023 plays dropped for a blank artist
--
-- Same cause as migration 036: the retired ingest skipped rows whose Artist
-- cell was empty. These two 2023 rows had the song folded into the Title;
-- the artist and title are as confirmed 2026-10-15:
--
--   2023-01-21 Freedom Swing Dance Challenge #29
--     "Pre-Released Song by Markus Smith" -> We Up (Pre Release) — NuChoyce
--   2023-08-11 Swing Fling #13
--     "LadyBriGoodMood Edited"           -> Good Mood (Edited) — Lady Bri
--
-- Each fills the gap it was skipped from (play_order is the row's position
-- in the file), with the length, BPM, year and play time from its row.
-- "(Pre Release)" and "(Edited)" are release labels, so each play files
-- under the song itself: the existing "We Up — NuChoyce" entry, and a new
-- "Good Mood — Lady Bri" entry. Catalog play counts and first/last played
-- are recomputed from the plays.
--
-- Keyed on production ids: a database without the sets changes nothing, and
-- a play already at its set and play_order is left alone, so a re-run
-- changes nothing.

INSERT INTO track_catalog (
  id, owner_id, title, artist, title_normalized, artist_normalized,
  source, confidence, bpm, release_year, play_count
)
SELECT gen_random_uuid(), s.owner_id, 'Good Mood', 'Lady Bri', 'good mood', 'lady bri',
       'play_history', 'low', 98.0, 2023, 0
FROM sets s
WHERE s.id = 'fa0e7c98-cb09-4896-bbeb-9fed0919300b'
  AND NOT EXISTS (
    SELECT 1 FROM track_catalog c
    WHERE c.title_normalized = 'good mood' AND c.artist_normalized = 'lady bri'
  );

CREATE TEMP TABLE restored_plays ON COMMIT DROP AS
SELECT s.id AS set_id, s.owner_id, p.play_order, p.play_time, p.title, p.artist,
       p.bpm, p.release_year, p.length_secs,
       (SELECT c.id FROM track_catalog c
        WHERE c.title_normalized = p.title_normalized
          AND c.artist_normalized = p.artist_normalized
        ORDER BY c.created_at, c.id LIMIT 1) AS catalog_id
FROM (VALUES
  ('482c36e5-6380-453d-9220-786a868c3c2f'::uuid, 29, TIME '03:40', 'We Up (Pre Release)', 'NuChoyce',
   110.0::double precision, NULL::int, 197, 'we up', 'nuchoyce'),
  ('fa0e7c98-cb09-4896-bbeb-9fed0919300b'::uuid, 13, TIME '15:52', 'Good Mood (Edited)', 'Lady Bri',
   98.0::double precision, 2023, 146, 'good mood', 'lady bri')
) AS p (set_id, play_order, play_time, title, artist, bpm, release_year, length_secs,
        title_normalized, artist_normalized)
JOIN sets s ON s.id = p.set_id
WHERE NOT EXISTS (
  SELECT 1 FROM tracks t WHERE t.set_id = p.set_id AND t.play_order = p.play_order
);

INSERT INTO tracks (
  id, owner_id, set_id, catalog_id, play_order, play_time, title, artist,
  bpm, release_year, length_secs, data_quality
)
SELECT gen_random_uuid(), r.owner_id, r.set_id, r.catalog_id, r.play_order, r.play_time,
       r.title, r.artist, r.bpm, r.release_year, r.length_secs, 'partial'
FROM restored_plays r;

UPDATE track_catalog c
SET play_count   = p.plays,
    first_played = p.first_played,
    last_played  = p.last_played
FROM (
  SELECT t.catalog_id, count(*) AS plays,
         min(s.set_date) AS first_played, max(s.set_date) AS last_played
  FROM tracks t
  JOIN sets s ON s.id = t.set_id
  WHERE t.catalog_id IN (SELECT catalog_id FROM restored_plays)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;
