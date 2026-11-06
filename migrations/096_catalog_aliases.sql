-- Migration 096: every spelling a catalog entry has been matched under
--
-- An entry remembers each (title, artist, remix) it has been matched under,
-- so a set import matches the current display or any earlier spelling, and
-- nothing that matched stops matching when an entry's display changes
-- (its parts are split, a rule changes, the owner edits it).
--
-- key_* are the spelling as the matcher compares it: lower case, runs of
-- whitespace as one space, trimmed (CatalogMatcher._row_key).
--
-- source says where a spelling came from:
--   play       a set import's play filed under the entry
--   live_play  a live play the lookup resolves to the entry
--   entry      the entry's own title and artist
--   display    the entry's display, written out from its parts
--
-- Backfilled here from the plays already filed (tracks) and from each
-- entry's own title and artist. Live plays are not linked to entries, so
-- they are backfilled by `python -m kaianolevine_api.cleanup.aliases`.

CREATE TABLE IF NOT EXISTS catalog_aliases (
  id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_id  UUID         NOT NULL REFERENCES track_catalog (id),
  title       TEXT         NOT NULL,
  artist      TEXT         NOT NULL,
  remix       TEXT,
  key_title   TEXT         NOT NULL,
  key_artist  TEXT         NOT NULL,
  key_remix   TEXT         NOT NULL DEFAULT '',
  source      TEXT         NOT NULL
                CHECK (source IN ('play', 'live_play', 'entry', 'display')),
  first_seen  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT uq_catalog_aliases_key_entry
    UNIQUE (key_title, key_artist, key_remix, catalog_id)
);

CREATE INDEX IF NOT EXISTS ix_catalog_aliases_catalog_id
  ON catalog_aliases (catalog_id);

INSERT INTO catalog_aliases
  (catalog_id, title, artist, remix, key_title, key_artist, key_remix, source)
SELECT DISTINCT ON (k.key_title, k.key_artist, k.key_remix, k.catalog_id)
  k.catalog_id, k.title, k.artist, k.remix, k.key_title, k.key_artist, k.key_remix, 'play'
FROM (
  SELECT t.catalog_id, t.title, t.artist, t.remix,
         regexp_replace(lower(btrim(t.title)), '\s+', ' ', 'g') AS key_title,
         regexp_replace(lower(btrim(t.artist)), '\s+', ' ', 'g') AS key_artist,
         regexp_replace(lower(btrim(coalesce(t.remix, ''))), '\s+', ' ', 'g') AS key_remix
  FROM tracks t
  WHERE t.catalog_id IS NOT NULL
) k
ORDER BY k.key_title, k.key_artist, k.key_remix, k.catalog_id
ON CONFLICT ON CONSTRAINT uq_catalog_aliases_key_entry DO NOTHING;

INSERT INTO catalog_aliases
  (catalog_id, title, artist, remix, key_title, key_artist, key_remix, source)
SELECT c.id, c.title, c.artist, c.remix,
       regexp_replace(lower(btrim(c.title)), '\s+', ' ', 'g'),
       regexp_replace(lower(btrim(c.artist)), '\s+', ' ', 'g'),
       regexp_replace(lower(btrim(coalesce(c.remix, ''))), '\s+', ' ', 'g'),
       'entry'
FROM track_catalog c
ON CONFLICT ON CONSTRAINT uq_catalog_aliases_key_entry DO NOTHING;
