-- Migration 044: file version plays from 2022 on under their version
--
-- A review on 2026-10-15 of every play from 2022 on found 129 plays filed
-- under the plain song although they are a version of it:
--   * 2024–2026 plays with the version in the Remix column (migration 039
--     covered only the 2022–2023 set files), e.g. Capricorn [Live Acoustic]
--     x13, Down Low [Penguin Prison Remix] x12, Marleyla [Swan.g Intro Edit]
--     x9, So Into You [Darkchild Main Version] x6;
--   * plays with "(Acoustic)" or "(Remix)" in the title, which the catalog's
--     title matching drops when it is the whole bracket, e.g. Mama (Acoustic)
--     x10, Boyfriend (Acoustic) x8, Stack It Up (Acoustic) x5.
-- The list was reviewed and approved play by play. Left alone on purpose:
-- DJ Don't (Remix) (merged with DJ Don't), release labels such as
-- [Titanic Soundtrack] and [SpiMan: Into The Spider-Verse], subtitles in the
-- Remix column, and plain plays filed under a version entry (open question).
--
-- Each play moves to its version's entry, "Title (Version)": the existing
-- one when the catalog has it (title + version matched ignoring case and
-- punctuation, and the main artist, splitting on "," "_" "&" "and" "feat."
-- "x" "with"), or a new one. Entries touched have play count and first/last
-- played recomputed from their plays; an entry left with no plays is
-- deleted. Plays are otherwise untouched.
--
-- Keyed on production play ids: a database without them changes nothing,
-- and a re-run finds the plays already filed and changes nothing.

CREATE TEMP TABLE version_plays (track_id uuid, display text) ON COMMIT DROP;

INSERT INTO version_plays (track_id, display) VALUES
  ('dac063dd-11e1-4df0-bebb-219924028deb', 'So Sick (Acoustic)'),
  ('ff19be7e-c15f-45f1-b6ef-ebf50c642599', 'Sexy Love (Acoustic)'),
  ('b02520c8-8e59-4442-88a0-69f6661eac55', 'Boyfriend (Acoustic)'),
  ('5c1cfd55-8ac8-480c-8b99-aedcf20b529d', 'So Sick (Acoustic)'),
  ('552bdf48-72ce-4c9a-8af9-0ba7061b53c3', 'Mama (Acoustic)'),
  ('0fabdee9-ac58-4db5-8c0b-1bd4e58b118f', 'Mixer (Acoustic)'),
  ('1d2e2539-9ddb-4846-8576-580979dc673a', 'Mixer (Acoustic)'),
  ('01bddc0a-4da2-44d5-9136-32a5c083ae79', 'Mama (Acoustic)'),
  ('fb053b82-1373-48be-bfa5-9472cc392a7d', 'Mama (Acoustic)'),
  ('f14408a0-462b-4c5a-859e-828c39b6a54f', 'Boyfriend (Acoustic)'),
  ('f460d0e2-fed5-4fbd-afe9-f9230529c3e0', 'Mama (Acoustic)'),
  ('e58b8ff1-132e-4dbf-ac49-915c9676e129', 'Mama (Acoustic)'),
  ('912a60eb-76b0-4cd3-8628-8de93edcfc45', 'Boyfriend (Acoustic)'),
  ('e799a6e7-60bc-4728-8050-fb89446ab99b', 'Mama (Acoustic)'),
  ('9388fb5c-f16d-4169-8ead-7291d191dc63', 'Lost In Japan (Remix)'),
  ('9f3948d5-f2d1-4284-94eb-b79217136756', 'Mama (Acoustic)'),
  ('8cdbe3ae-7dec-47b8-b8a2-8ffbb775ee77', 'Mama (Acoustic)'),
  ('67f16afe-a8d8-4b2d-b3ce-b9ac463074d3', 'Mama (Acoustic)'),
  ('9f455ab4-3b74-40d8-904f-841033b9a2e3', 'Boyfriend (Acoustic)'),
  ('44dc0190-45d8-43ba-9611-9f6a984dd34d', 'Mama (Acoustic)'),
  ('f291cdf3-7f6e-4a60-8d7b-8023523ce0aa', 'Boyfriend (Acoustic)'),
  ('c2b2b676-474b-4d25-a9ea-1ac52c698426', 'Boyfriend (Acoustic)'),
  ('77b67654-bf1a-4d7c-b246-048538312b9b', 'Boyfriend (Acoustic)'),
  ('a1149abf-b497-4ebd-89d1-19b9b1cfaa7d', 'Shape Of You (Acoustic)'),
  ('94dce1c0-e1ae-4b08-a360-d7841bc6c614', 'Shape Of You (Acoustic)'),
  ('741c42f6-5082-4629-acf7-7b1806c2a1da', 'Lost In Japan (Remix)'),
  ('abbacc93-1794-4d84-8b08-f660b6d713a2', 'Temple (Lenno Remix)'),
  ('d4b4d28a-c6a7-4f2d-bf6e-82dc9262790b', 'Cruel (Piano Acoustic)'),
  ('18d528b6-56c0-4fa4-9b7e-566911f52493', 'Cold Heart (Pnau Remix)'),
  ('410d8664-103d-48ab-8e6e-bad6bcf0ced6', 'California Girls (NoMBe vs Sonny Alven Remix)'),
  ('337cfffc-3ba0-4de4-aa58-d7dc6106d3c5', 'Cold Heart (Pnau Remix)'),
  ('970e1612-a254-4bc2-9a4e-72993fe55d0e', 'So Into You (Darkchild Main Version)'),
  ('b54bba09-161a-442e-899a-58fbe1297cc3', 'Cruel (Piano Acoustic)'),
  ('a2ff5b2b-dd18-4634-bc71-1c9be5e7adbb', 'Mama (Acoustic)'),
  ('ab9bdabf-3ab4-4171-829a-d79f4be1f289', 'I Didn''t Know (Acoustic)'),
  ('87998026-ccbc-4d2f-bc3c-c8976c32feb9', 'I Didn''t Know (Acoustic)'),
  ('5bdcc93e-c1ef-42bf-b376-ac37bd66237b', 'Down Low (Penguin Prison Remix)'),
  ('dbd72e04-d201-4be9-a7cd-cb223b61ab2c', 'Shape Of You (Acoustic)'),
  ('45d6d48a-2673-4ff3-948a-9e497861df7d', 'Stack It Up (Acoustic)'),
  ('38c6e320-9ef3-48c4-bd1f-060363959951', 'Down Low (Penguin Prison Remix)'),
  ('e2ca3c75-4653-4557-89d5-3a7f48219d38', 'Unstoppable (FKJ Remix)'),
  ('f9ab138b-16cc-407e-aea0-37e3f7099636', 'So Into You (Darkchild Main Version)'),
  ('7cdd0557-0254-4180-9058-e24e22bfdee8', 'Down Low (Penguin Prison Remix)'),
  ('04bb5d5b-7bf2-40b7-af7e-630c82009ae7', 'Down Low (Penguin Prison Remix)'),
  ('b02e0ba8-c7a9-4512-a792-8cbf8db78647', 'Bonbon (Post Malone Remix)'),
  ('25952207-feab-4a31-874d-a7fbe17cc777', 'Talk Slow (Sistek Remix)'),
  ('01997660-f6e6-4cfb-8479-ee0ffdb1e061', 'Marleyla (Swan.g Intro Edit)'),
  ('27039409-d55e-4405-a169-933a26269a07', 'Mood (Remix & Radio Edit)'),
  ('d86982e9-ebc6-4146-b17c-d5e2ee5477e4', 'California Girls (NoMBe vs Sonny Alven Remix)'),
  ('6872f0be-74a9-40b6-aab4-6a406a154bcc', 'Down Low (Penguin Prison Remix)'),
  ('7eb66001-db46-4e74-9ed3-ab03cc836c4e', 'Slow Hands (Basic Tape Remix)'),
  ('12e03ee7-951f-42e1-ad42-5e506bcc03ac', 'Be Kind (Stripped)'),
  ('81fd02bb-9f5f-4571-a8fb-418a9853ba3c', 'Cruel (Piano Acoustic)'),
  ('32aa0f43-b965-46a8-821e-36bf884ed76d', 'Capricorn (Live Acoustic)'),
  ('af432b4c-d3b6-4dc2-9cca-71bdc00be8d0', 'Shape of You (Cover Remix)'),
  ('819fa257-7427-4ff0-bd6a-b79b7920bf6c', 'Capricorn (Live Acoustic)'),
  ('2c528efb-80bd-432b-ba52-90f4f60c9283', 'Hound Dog (The Voice Performance)'),
  ('1d66d954-ff09-4ea8-9f8c-01733737f873', 'Lie (Tep No Remix)'),
  ('924777e8-1e09-45c6-811e-d22bd81d8dab', 'The Other (Stripped)'),
  ('b93f92bd-853a-4f65-b649-bda392d1d7d3', 'Down Low (Penguin Prison Remix)'),
  ('51412da3-1518-401a-b3b9-7c9dd64d22e8', 'Lost (Lucian Remix)'),
  ('55784c1f-4e23-45a5-a66f-54d2464ed0ab', 'Bonbon (Post Malone Remix)'),
  ('a367f02a-ced8-4985-a0ec-1c2376d7bbba', 'Capricorn (Live Acoustic)'),
  ('5f845ea6-6526-49cd-8026-e5d187dda42d', 'Down Low (Penguin Prison Remix)'),
  ('04a6fecb-c8e6-4956-850f-1da6df65fe76', 'Jump (Acoustic)'),
  ('94328f90-f248-4bc4-99ab-bbd2565caeb4', 'Down Low (Penguin Prison Remix)'),
  ('2c7b1fbd-5483-456d-8a32-6c204695772a', 'Miss Yo (Aslove Remix)'),
  ('1b0d39b2-66d9-4602-8473-59e345f14ea6', 'Just You and I (Acoustic)'),
  ('436a9e99-e3e4-40a0-a2fa-f3536d9d7918', 'Down Low (Penguin Prison Remix)'),
  ('7ee4df6e-e1cf-4a9f-8c89-75cd4a1f5c19', 'Cruel (Piano Acoustic)'),
  ('bf2b51a1-de92-4a42-9655-8ba99db8b390', 'Hound Dog (The Voice Performance)'),
  ('4b7f2eeb-0277-4ed6-a38e-8d9672b405ec', 'Miss Yo (Aslove Remix)'),
  ('80b6d560-0266-4b5a-a190-389d7440685d', 'Capricorn (Live Acoustic)'),
  ('e40e3ca6-56d4-4d9a-854d-4704c3ba6fa8', 'Down Low (Penguin Prison Remix)'),
  ('48ed6f45-6124-4373-8e7b-fa49f3e70071', 'Marleyla (Swan.g Intro Edit)'),
  ('708aadf6-3840-429c-8f0f-54cac1140bc7', 'Talk Slow (Sistek Remix)'),
  ('4f9f5672-9de4-4b6a-af28-f61a63304f60', 'Down Low (Penguin Prison Remix)'),
  ('6fae9ce9-4cd4-4958-b4a3-eedd3c2cb853', 'Capricorn (Live Acoustic)'),
  ('31379a7b-6b43-49fa-a82e-bebfee1fea8a', 'Slow Hands (Basic Tape Remix)'),
  ('e34494e2-7f9d-41b1-84e5-2d312bb7a023', 'Shape Of You (Acoustic)'),
  ('ff4f01d4-de6f-4f16-95ea-230befe36358', 'Stack It Up (Acoustic)'),
  ('46200eaa-a0a1-472b-b5f9-414b47927232', 'Most Girls (Acoustic)'),
  ('da141987-106e-43d9-a267-bcb89280b16f', 'Bonbon (Post Malone Remix)'),
  ('9fb8c429-c378-4446-9a15-f9b750259854', 'Talk Slow (Sistek Remix)'),
  ('6a631b8d-8dd6-41f5-a4ee-ca74d2d1b84b', 'Slow Hands (Basic Tape Remix)'),
  ('4158dd62-81c5-42e1-b386-4499ea4819c0', 'I Like Me Better (Recorded at Spotify Studios NYC)'),
  ('7c37fe2e-135c-4544-89ec-9d5ac77a725f', 'Capricorn (Live Acoustic)'),
  ('56cc6adc-5a58-4377-a92c-a7a115fd3f8b', 'Just You and I (Acoustic)'),
  ('eba1674f-94f7-486e-b574-4496c41acaf1', 'Hound Dog (The Voice Performance)'),
  ('bf2bdd66-1c63-4b11-9840-0571d6cb119f', 'Free Fallin'' (Live)'),
  ('2b44f3c6-c810-4763-8c65-1f102fc60d5e', 'Talk Slow (Sistek Remix)'),
  ('7dfa019c-aa5b-4279-9fff-32bd9fcacaef', 'I Like Me Better (Recorded at Spotify Studios NYC)'),
  ('50a7d6c1-b044-4e57-ac0b-4f64ce5543f5', 'So Into You (Darkchild Main Version)'),
  ('5484bae9-d13a-46ac-8f38-bb5d48467fd1', 'Capricorn (Live Acoustic)'),
  ('cc8abce1-032f-49b8-9508-834c2036ea5f', 'Marleyla (Swan.g Intro Edit)'),
  ('f50f2858-41e7-4eee-aab8-ef7193d8018b', 'Hymn For The Weekend (Seeb Remix)'),
  ('b3410647-d6d3-4c29-b338-2eaa6ceb2aa2', 'Marleyla (Swan.g Intro Edit)'),
  ('21a7adc9-b40a-4892-aa24-f4efe8c9633f', 'Capricorn (Live Acoustic)'),
  ('b2fb90ec-8f9e-4132-892d-b97254f5706f', 'Marleyla (Swan.g Intro Edit)'),
  ('93bb90cc-f599-470d-b2fb-81c18c647ed5', 'The Weight Is Gone (The Purgatory Sessions)'),
  ('001c15d8-5298-4e04-82cf-1e31d3a908c8', 'Capricorn (Live Acoustic)'),
  ('4dface53-007d-4dfd-9167-bf3fa1a430ac', 'Twerk It Like Miley (Dawin Remix feat. Christopher & Dawin)'),
  ('43961fdd-5939-40f9-8c31-8df499f865cd', 'Marleyla (Swan.g Intro Edit)'),
  ('17fc56eb-5b87-4062-952e-fbeec5a7fe8b', 'Capricorn (Live Acoustic)'),
  ('d70916f4-aeef-49d8-8275-5d128538adc5', 'Marleyla (Swan.g Intro Edit)'),
  ('f312170d-542b-4509-b085-60dc33f240ba', 'Capricorn (Live Acoustic)'),
  ('5bb568df-0e85-44d3-9685-b9d189c3fbb3', 'Stack It Up (Acoustic)'),
  ('9913919b-93ce-4132-b858-bb7fa069730a', 'Most Girls (Acoustic)'),
  ('770ccca7-e388-4666-b47c-7cfbfcf6b07e', 'Capricorn (Live Acoustic)'),
  ('0cdfe033-5550-4c53-932f-c5f90ad987bd', 'Marleyla (Swan.g Intro Edit)'),
  ('6397af0f-8b1e-47e9-b848-427025ebbc5b', 'Fresh Prince Of Bel Air (Le Boeuf Remix)'),
  ('497bed41-a025-43c1-b004-8bfd5254c22a', 'Cold Heart (Pnau Remix)'),
  ('5158acd2-4ce0-4147-9578-f586c6cde93a', 'Most Girls (Acoustic)'),
  ('2c9af238-6988-4e18-884a-5a81e5ae07c8', 'Stack It Up (Acoustic)'),
  ('0c1e09e2-5246-458e-b9d1-56af2bce23e6', 'Cold Heart (Pnau Remix)'),
  ('7b85a7a9-a8ce-466a-b6d7-7345fb9f9d9b', 'Down Low (Penguin Prison Remix)'),
  ('472a690e-abec-4523-ba84-7cc63b31899b', 'So Into You (Darkchild Main Version)'),
  ('04b3b6b5-ed0a-49ad-8b76-f952e8af787d', 'I Didn''t Know (Acoustic)'),
  ('720c246a-565c-441f-83d6-9b6d258829a3', 'Capricorn (Live Acoustic)'),
  ('718b8167-d27e-4b29-b3af-1c2c3d8f0935', 'Boyfriend (Acoustic)'),
  ('71036461-5eb9-4dc3-b56c-c7dda61ef3c7', 'Dancing With A Stranger (Acoustic)'),
  ('b69fc0fd-896c-4685-ad42-2fa9f22c8d73', 'Attention (Acoustic)'),
  ('d7dc053a-b73b-4aa8-8db9-4434afd7610d', '5:19 (Acoustic)'),
  ('9f5d59b0-8ea7-47cb-82c6-fde5ae820e36', 'Stack It Up (Acoustic)'),
  ('c49fbc8e-29e4-4a52-9e1c-b3b94de75ce5', 'Paris (Yo Scheppert Remix)'),
  ('a4f25841-c66f-4bee-b0cb-78f2412040bd', 'Marleyla (Swan.g Intro Edit)'),
  ('348fbd06-91ec-4f39-a570-b2b840de4d76', 'So Into You (Darkchild Main Version)'),
  ('18225884-b21e-4978-adb4-e65d54e4ac62', 'So Into You (Darkchild Main Version)'),
  ('c540b815-5a89-4043-983a-915108992c48', 'Nervous - The Ooh Song (Mark Mccabe Remix)');

CREATE TEMP TABLE version_targets ON COMMIT DROP AS
SELECT
  t.id AS track_id,
  c.id AS from_id,
  c.owner_id,
  c.artist,
  c.artist_normalized,
  v.display,
  regexp_replace(regexp_replace(lower(v.display), '[^a-z0-9]+', '', 'g'), 'version$', '') AS title_key,
  regexp_replace(regexp_replace(lower(c.artist), '\s*(,|_|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''), '[^a-z0-9]+', '', 'g') AS artist_key
FROM version_plays v
JOIN tracks t ON t.id = v.track_id
JOIN track_catalog c ON c.id = t.catalog_id;

CREATE TEMP TABLE version_catalog_keys ON COMMIT DROP AS
SELECT
  c.id, c.artist_normalized, c.play_count, c.created_at,
  regexp_replace(regexp_replace(lower(c.title || coalesce(' ' || c.remix, '')), '[^a-z0-9]+', '', 'g'), 'version$', '') AS title_key,
  regexp_replace(regexp_replace(lower(c.artist), '\s*(,|_|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''), '[^a-z0-9]+', '', 'g') AS artist_key
FROM track_catalog c;

-- Versions the catalog does not have yet, one entry each.
INSERT INTO track_catalog (
  id, owner_id, title, artist, title_normalized, artist_normalized,
  source, confidence, play_count
)
SELECT DISTINCT ON (r.title_key, r.artist_key)
  gen_random_uuid(), r.owner_id, r.display, r.artist, lower(r.display), r.artist_normalized,
  'play_history', 'low', 0
FROM version_targets r
WHERE NOT EXISTS (
  SELECT 1 FROM version_catalog_keys k
  WHERE k.title_key = r.title_key AND k.artist_key = r.artist_key
)
ORDER BY r.title_key, r.artist_key, r.display
ON CONFLICT (owner_id, title_normalized, artist_normalized) DO NOTHING;

INSERT INTO version_catalog_keys
SELECT
  c.id, c.artist_normalized, c.play_count, c.created_at,
  regexp_replace(regexp_replace(lower(c.title || coalesce(' ' || c.remix, '')), '[^a-z0-9]+', '', 'g'), 'version$', ''),
  regexp_replace(regexp_replace(lower(c.artist), '\s*(,|_|&|\mand\M|\mfeat\.?|\mft\.?|\mfeaturing\M|\mx\M|\mwith\M).*$', ''), '[^a-z0-9]+', '', 'g')
FROM track_catalog c
WHERE c.id NOT IN (SELECT id FROM version_catalog_keys);

-- Each play's entry: the match sharing its exact artist first, then the
-- most played, then the oldest.
CREATE TEMP TABLE version_moves ON COMMIT DROP AS
SELECT r.track_id, r.from_id, m.id AS to_id
FROM version_targets r
CROSS JOIN LATERAL (
  SELECT k.id FROM version_catalog_keys k
  WHERE k.title_key = r.title_key AND k.artist_key = r.artist_key
  ORDER BY (k.artist_normalized = r.artist_normalized) DESC, k.play_count DESC, k.created_at, k.id
  LIMIT 1
) m
WHERE m.id <> r.from_id;

UPDATE tracks t
SET catalog_id = m.to_id
FROM version_moves m
WHERE t.id = m.track_id;

-- New entries take their metadata from their plays.
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
  WHERE t.catalog_id IN (SELECT to_id FROM version_moves)
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
  WHERE t.catalog_id IN (SELECT to_id FROM version_moves UNION SELECT from_id FROM version_moves)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;

DELETE FROM track_catalog c
WHERE c.id IN (SELECT from_id FROM version_moves)
  AND NOT EXISTS (SELECT 1 FROM tracks t WHERE t.catalog_id = c.id);
