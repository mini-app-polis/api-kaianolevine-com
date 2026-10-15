-- Migration 045: split plain plays out of six version entries
--
-- The catalog's title matching drops "(Acoustic)" / "(Remix)" brackets and
-- everything after "feat.", so these version entries were keyed under the
-- plain song and collected plain plays too. Reviewed 2026-10-15: the plain
-- plays are the original song, not shorthand for the version.
--
--   king (acoustic) — slenderbodies                  13 plain plays (2021–2023)
--   It's You (Acoustic) — Ali Gatie                    4 (2022–2024)
--   Girlfriend at the Time (Acoustic) — Emily Burns     5 (2021–2022)
--   Floating (feat. Khalid) [filous Remix] — Alina Baraz 4 (2019–2024)
--   No Sleeep (ft. J. Cole) [Afsheen Remix] — Janet Jackson 1 (2022)
--   Emiliana (Acoustic) — CKay                          1 (2023)
--
-- (Black Coffee In Bed (Acoustic) — Ernie Halter keeps its plain play: the
-- one case confirmed to be the acoustic version.)
--
-- 1. Each of the six version entries is re-keyed under its full title, so the
--    stats (which group songs by normalized title and artist) no longer fold
--    the original into it.
-- 2. The plain plays move to the original's entry: the existing one when the
--    catalog has it (title matched ignoring case and punctuation, and the main
--    artist), otherwise a new one keyed the way the catalog keys any title.
-- 3. Play count and first/last played are recomputed for every entry touched.
--
-- Keyed on production ids: a database without them changes nothing, and a
-- re-run finds the plays already moved and changes nothing.

UPDATE track_catalog
SET title_normalized = lower(title)
WHERE id IN (
  'ac55f86e-3f41-4962-9cb1-2b4086347509',
  '365ba66d-9682-40d3-ba81-fa9488616ed2',
  '2005118f-6e6b-4fee-b4e5-503138589532',
  'c66f1b39-427d-473d-bb45-ad2eaf54d478',
  'f9c32576-227d-4ad1-bd99-85a81ecef08f',
  'a845b220-6127-4342-92a2-bdd0c2d7f1bb'
)
  AND title_normalized <> lower(title);

CREATE TEMP TABLE plain_plays (track_id uuid, display text, title_normalized text) ON COMMIT DROP;

INSERT INTO plain_plays (track_id, display, title_normalized) VALUES
  ('4206cc18-12b7-4cd8-8a88-c20024c11dab', 'It''s You', 'it''s you'),
  ('af4b64dc-da3b-47e6-a293-8239078df4ae', 'It''s You', 'it''s you'),
  ('83b45d82-ec3c-43fb-a40e-2bf80bd7f9f6', 'It''s You', 'it''s you'),
  ('f4f648d8-b105-4453-b0ca-e8c26e760f94', 'It''s You', 'it''s you'),
  ('c6d09449-0d1c-4bc2-a5c3-0ab0a3a1e6f2', 'king', 'king'),
  ('da06262f-0cda-42dc-b26c-5d18b601ea98', 'king', 'king'),
  ('76679641-e103-4f7e-a2f0-862b63af6e9f', 'king', 'king'),
  ('7a4660b1-9d41-4c39-b0a6-141d8ca9e651', 'king', 'king'),
  ('1eaa38de-fd63-47e7-84d6-3bfcda35838b', 'king', 'king'),
  ('88ad14ab-3a17-420d-ad4c-3929c69b1d39', 'king', 'king'),
  ('9f9622e8-a201-48d1-afc7-371a2f3b0f47', 'king', 'king'),
  ('3c4c4fff-54b7-4df3-ba61-6c0ece1b97b6', 'king', 'king'),
  ('f35c3857-b431-49da-8e4a-7c1d35685060', 'king', 'king'),
  ('8db9b4cc-b90e-4750-827c-6bb02f258e1f', 'king', 'king'),
  ('6d78ae99-0f1f-405f-a166-349bca9a9c7c', 'king', 'king'),
  ('61f4d963-6e05-477c-8165-eef434f58954', 'king', 'king'),
  ('eebe80fa-29d3-40d3-9e64-40307f8ea4e4', 'king', 'king'),
  ('693f3493-5ae9-4488-9395-75d60e9a6bc9', 'Floating (Feat. Khalid)', 'floating'),
  ('7c768742-4c45-4baa-a696-aa0040d97b54', 'Floating (Feat. Khalid)', 'floating'),
  ('12536ee1-caa1-4259-bea3-730699ee1d0f', 'Floating (Feat. Khalid)', 'floating'),
  ('d1651d0b-544d-428d-84d5-3442b28bb609', 'Floating (Feat. Khalid)', 'floating'),
  ('239999c8-8e49-4084-b951-dce2f4569f55', 'No Sleeep', 'no sleeep'),
  ('560af2d6-f8f1-4733-8d01-996e65ff1fda', 'Girlfriend at the Time', 'girlfriend at the time'),
  ('b5717ef1-6d04-4133-8779-9d4b7bfa7368', 'Girlfriend at the Time', 'girlfriend at the time'),
  ('ef1aa6e1-3c6c-43ae-8245-4c477abc8bbc', 'Girlfriend at the Time', 'girlfriend at the time'),
  ('59085f85-f9f3-415a-8be4-f4325b50da31', 'Girlfriend at the Time', 'girlfriend at the time'),
  ('07885525-aec5-4127-83ed-38e6b3eaf820', 'Girlfriend at the Time', 'girlfriend at the time'),
  ('6aed8c15-0aae-456a-b8bd-d407db2f1e75', 'Emiliana', 'emiliana');

CREATE TEMP TABLE plain_targets ON COMMIT DROP AS
SELECT
  t.id AS track_id,
  c.id AS from_id,
  c.owner_id,
  c.artist,
  c.artist_normalized,
  p.display,
  p.title_normalized,
  regexp_replace(lower(p.display), '[^a-z0-9]+', '', 'g') AS title_key,
  regexp_replace(regexp_replace(lower(c.artist), '\s*(,|_|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''), '[^a-z0-9]+', '', 'g') AS artist_key
FROM plain_plays p
JOIN tracks t ON t.id = p.track_id
JOIN track_catalog c ON c.id = t.catalog_id;

CREATE TEMP TABLE plain_catalog_keys ON COMMIT DROP AS
SELECT
  c.id, c.artist_normalized, c.play_count, c.created_at,
  regexp_replace(lower(c.title || coalesce(' ' || c.remix, '')), '[^a-z0-9]+', '', 'g') AS title_key,
  regexp_replace(regexp_replace(lower(c.artist), '\s*(,|_|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''), '[^a-z0-9]+', '', 'g') AS artist_key
FROM track_catalog c;

INSERT INTO track_catalog (
  id, owner_id, title, artist, title_normalized, artist_normalized,
  source, confidence, play_count
)
SELECT DISTINCT ON (r.title_key, r.artist_key)
  gen_random_uuid(), r.owner_id, r.display, r.artist, r.title_normalized, r.artist_normalized,
  'play_history', 'low', 0
FROM plain_targets r
WHERE NOT EXISTS (
  SELECT 1 FROM plain_catalog_keys k
  WHERE k.title_key = r.title_key AND k.artist_key = r.artist_key
)
ORDER BY r.title_key, r.artist_key
ON CONFLICT (owner_id, title_normalized, artist_normalized) DO NOTHING;

INSERT INTO plain_catalog_keys
SELECT
  c.id, c.artist_normalized, c.play_count, c.created_at,
  regexp_replace(lower(c.title || coalesce(' ' || c.remix, '')), '[^a-z0-9]+', '', 'g'),
  regexp_replace(regexp_replace(lower(c.artist), '\s*(,|_|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''), '[^a-z0-9]+', '', 'g')
FROM track_catalog c
WHERE c.id NOT IN (SELECT id FROM plain_catalog_keys);

CREATE TEMP TABLE plain_moves ON COMMIT DROP AS
SELECT r.track_id, r.from_id, m.id AS to_id
FROM plain_targets r
CROSS JOIN LATERAL (
  SELECT k.id FROM plain_catalog_keys k
  WHERE k.title_key = r.title_key AND k.artist_key = r.artist_key
  ORDER BY (k.artist_normalized = r.artist_normalized) DESC, k.play_count DESC, k.created_at, k.id
  LIMIT 1
) m
WHERE m.id <> r.from_id;

UPDATE tracks t
SET catalog_id = m.to_id
FROM plain_moves m
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
  WHERE t.catalog_id IN (SELECT to_id FROM plain_moves)
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
  WHERE t.catalog_id IN (SELECT to_id FROM plain_moves UNION SELECT from_id FROM plain_moves)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;
