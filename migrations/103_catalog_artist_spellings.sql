-- Migration 103: one spelling for each artist name, as the artist styles it
--
-- A read on 2026-11-08 of every live entry's main and featured artists found
-- 17 names spelled with more than one capitalization across entries
-- ("Dnce" and "DNCE", "Quinn Xcii" and "Quinn XCII"), and two spelled only
-- wrong ("Onerepublic", "Travie Mccoy"). The display rules keep case as
-- stored, so a wrong spelling reached the music files. The owner chose the
-- spelling for each, following the artist's own styling.
--
-- Only the parts change: each name in main_artists and featured_artists is
-- replaced where it matches the name ignoring case. The title and artist
-- shown are written out from the parts, so they change everywhere; the
-- entry's keys (title_normalized, artist_normalized) are lowercase and do
-- not. updated_at is set on each entry changed, so the library sync writes
-- the new spelling to the files linked to it.
--
-- A re-run finds every name already spelled as chosen and changes nothing.

CREATE TEMP TABLE spelling_103 (name text, spelled text) ON COMMIT DROP;

INSERT INTO spelling_103 (name, spelled) VALUES
  ('banks', 'BANKS'),
  ('christine and the queens', 'Christine and the Queens'),
  ('ckay', 'CKay'),
  ('dj khaled', 'DJ Khaled'),
  ('dnce', 'DNCE'),
  ('earth wind and fire', 'Earth Wind and Fire'),
  ('gnash', 'gnash'),
  ('goldfish', 'GoldFish'),
  ('great good fine ok', 'Great Good Fine OK'),
  ('mike.', 'mike.'),
  ('prettymuch', 'PRETTYMUCH'),
  ('quinn xcii', 'Quinn XCII'),
  ('roomful of blues', 'Roomful of Blues'),
  ('slenderbodies', 'slenderbodies'),
  ('trinix', 'TRINIX'),
  ('troyboi', 'TroyBoi'),
  ('upsahl', 'UPSAHL'),
  ('onerepublic', 'OneRepublic'),
  ('travie mccoy', 'Travie McCoy');

UPDATE track_catalog c
SET main_artists = fixed.main_artists,
    featured_artists = fixed.featured_artists,
    updated_at = now()
FROM (
  SELECT
    c2.id,
    ARRAY(
      SELECT coalesce(s.spelled, a.name)
      FROM unnest(c2.main_artists) WITH ORDINALITY AS a(name, n)
      LEFT JOIN spelling_103 s ON s.name = lower(a.name)
      ORDER BY a.n
    ) AS main_artists,
    ARRAY(
      SELECT coalesce(s.spelled, a.name)
      FROM unnest(c2.featured_artists) WITH ORDINALITY AS a(name, n)
      LEFT JOIN spelling_103 s ON s.name = lower(a.name)
      ORDER BY a.n
    ) AS featured_artists
  FROM track_catalog c2
) AS fixed
WHERE c.id = fixed.id
  AND (c.main_artists IS DISTINCT FROM fixed.main_artists
       OR c.featured_artists IS DISTINCT FROM fixed.featured_artists);
