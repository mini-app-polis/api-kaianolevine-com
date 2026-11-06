-- Migration 093: catalog entries store a song's parts; display rules write them out
--
-- An entry holds the parts of a song separately: its base title, its version
-- (a remix, an acoustic or live take), its main artists and its featured
-- artists, in order, and its genre and subgenre. The title and artist shown
-- for it are written out from the parts by the display rules, one set for the
-- whole catalog, so a change to how songs are shown is a change to a rule,
-- not an edit to every entry.
--
-- The parts start empty. Until an entry's are filled (base_title set and at
-- least one main artist), readers show its title and artist columns as they
-- are today, and those columns stay until the split migration fills every
-- entry's parts. Matching is unchanged: it keys on title_normalized and
-- artist_normalized.

ALTER TABLE track_catalog
  ADD COLUMN IF NOT EXISTS base_title        TEXT,
  ADD COLUMN IF NOT EXISTS version           TEXT,
  ADD COLUMN IF NOT EXISTS main_artists      TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS featured_artists  TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS subgenre          TEXT;

-- The one set of display rules. A single row (id = 1); changed_at says when
-- a rule last changed.
--
--   featured_placement  where featured artists go: in the artist
--                       ("Pink Sweat$ feat. Kehlani") or after the title
--   featured_word       the word before them
--   artist_joiner       between several main artists, and several featured
--   version_open/close  around the version: "Attention (Lash Remix)"
--   bpm_decimals        BPM is shown to this many decimals
--   case_style          as_styled: capitalisation kept as the artist styles it
CREATE TABLE IF NOT EXISTS display_rules (
  id                  SMALLINT     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  featured_placement  TEXT         NOT NULL DEFAULT 'artist'
                        CHECK (featured_placement IN ('artist', 'title')),
  featured_word       TEXT         NOT NULL DEFAULT 'feat.' CHECK (featured_word <> ''),
  artist_joiner       TEXT         NOT NULL DEFAULT ', ' CHECK (artist_joiner <> ''),
  version_open        TEXT         NOT NULL DEFAULT '(',
  version_close       TEXT         NOT NULL DEFAULT ')',
  bpm_decimals        SMALLINT     NOT NULL DEFAULT 1 CHECK (bpm_decimals BETWEEN 0 AND 3),
  case_style          TEXT         NOT NULL DEFAULT 'as_styled'
                        CHECK (case_style IN ('as_styled')),
  changed_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

INSERT INTO display_rules (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
