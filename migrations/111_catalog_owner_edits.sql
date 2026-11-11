-- Migration 111: catalog entries can be owner edits; display rules mark them
--
-- A file the owner changed by hand (an intro cut, words censored) is an
-- owner edit: not the published recording, and not a release label
-- (deejay-cog ADR-0008, owner edits). It is a version with an entry of its
-- own, and the entry records that its version is an owner edit. cut_from_id
-- links it to the published entry it was cut from, when that is known.
--
-- The display rules gain an owner-edit pair beside the version pair, so an
-- owner edit is written "Baby What You Want Me To Do <Intro Cut>".
--
-- Additive: every existing entry stays unflagged with no link, and every
-- existing display is unchanged.

ALTER TABLE track_catalog
  ADD COLUMN IF NOT EXISTS owner_edit   BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS cut_from_id  UUID REFERENCES track_catalog(id);

ALTER TABLE display_rules
  ADD COLUMN IF NOT EXISTS owner_edit_open   TEXT NOT NULL DEFAULT '<'
    CHECK (owner_edit_open <> ''),
  ADD COLUMN IF NOT EXISTS owner_edit_close  TEXT NOT NULL DEFAULT '>'
    CHECK (owner_edit_close <> '');
