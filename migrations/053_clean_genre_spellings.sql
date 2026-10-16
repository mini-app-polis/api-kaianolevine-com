-- Migration 053: one spelling per genre, and no values that are not genres
--
-- The set files spell some genres several ways, and some carry a value that
-- is not a genre. Approved 2026-10-15; the ingest now cleans genres the same
-- way (services/song_identity.clean_genre):
--   * a tempo suffix is dropped: "Pop - 100" -> "Pop", "Christmas - 102" ->
--     "Christmas";
--   * values that are not genres are cleared: "---", "-148", "-148.0",
--     "(148)", "2020s", "Unknown";
--   * variant spellings take the one used most: "pop" -> "Pop",
--     "Alternative-Rock" -> "Alternative Rock", "Dance-pop" / "Dance-Pop" ->
--     "Dance/Pop", "ElectroPop" -> "Electropop", "Hip Hop" -> "Hip-Hop",
--     "Jazz & Funk" -> "Jazz-funk", "Singer/Songwriter" -> "Singer-Songwriter",
--     "Contemporary R&b" -> "Contemporary R&B", "Soundtracks" -> "Soundtrack",
--     "Instrument" -> "Instrumental".
-- Different genres ("Rap" and "Rap & Hip-Hop", say) are left as they are.
-- Applied to plays and catalog entries alike. A re-run changes nothing.

CREATE TEMP TABLE genre_053 (old_genre text, new_genre text) ON COMMIT DROP;

INSERT INTO genre_053 (old_genre, new_genre) VALUES
  ('Pop - 100', 'Pop'),
  ('Christmas - 102', 'Christmas'),
  ('---', NULL),
  ('-148', NULL),
  ('-148.0', NULL),
  ('(148)', NULL),
  ('2020s', NULL),
  ('Unknown', NULL),
  ('pop', 'Pop'),
  ('Alternative-Rock', 'Alternative Rock'),
  ('Dance-pop', 'Dance/Pop'),
  ('Dance-Pop', 'Dance/Pop'),
  ('ElectroPop', 'Electropop'),
  ('Hip Hop', 'Hip-Hop'),
  ('Jazz & Funk', 'Jazz-funk'),
  ('Singer/Songwriter', 'Singer-Songwriter'),
  ('Contemporary R&b', 'Contemporary R&B'),
  ('Soundtracks', 'Soundtrack'),
  ('Instrument', 'Instrumental');

UPDATE tracks t
SET genre = g.new_genre
FROM genre_053 g
WHERE t.genre = g.old_genre;

UPDATE track_catalog c
SET genre = g.new_genre
FROM genre_053 g
WHERE c.genre = g.old_genre;
