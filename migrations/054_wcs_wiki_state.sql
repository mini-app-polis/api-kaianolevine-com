-- Migration 054: the wiki curator's state
--
-- wiki-curator-cog's three passes each keep what they decided and the prompt
-- version they decided it under, so a re-run pays only for what is new:
--
--   linking        which concepts each statement refers to (or denies)
--   consolidation  which entities fold into a broader one
--   generation     each entity's prose, the statements it has integrated,
--                  and every revision of it
--
-- That state lived in three JSON files in the wcs-wiki repo and was paid for
-- in model calls. These tables hold it in the API that owns the rest of the
-- WCS data (wiki-curator-cog ADR-0006). They are keyed exactly as the
-- files are — a statement key such as 'attribution:<uuid>', or an entity
-- slug — so the files import as they stand and every watermark still holds.
--
-- No foreign keys to wcs_entities or wcs_sources, on purpose. Concepts the
-- linker proposed have no entity row and live here only, by slug; and a
-- source re-extracted or removed upstream must not take the decisions made
-- about it with it. The curator ignores keys the corpus no longer has.

-- The non-entry fields of each document: its version(s) and model as of the
-- last run.
CREATE TABLE IF NOT EXISTS wcs_wiki_state_meta (
  name       TEXT PRIMARY KEY CHECK (name IN ('links', 'facets', 'prose')),
  header     JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Linking: one row per statement read, and one per reference it makes.
CREATE TABLE IF NOT EXISTS wcs_wiki_statements (
  statement_key  TEXT PRIMARY KEY,
  subject_slug   TEXT NOT NULL,
  source_id      UUID,
  session_date   DATE,
  text           TEXT NOT NULL,
  linker_version TEXT NOT NULL,
  model          TEXT NOT NULL DEFAULT '',
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_wcs_wiki_statements_subject
  ON wcs_wiki_statements (subject_slug);

CREATE TABLE IF NOT EXISTS wcs_wiki_references (
  statement_key    TEXT NOT NULL
    REFERENCES wcs_wiki_statements (statement_key) ON DELETE CASCADE,
  position         INTEGER NOT NULL,
  name             TEXT NOT NULL,
  target_slug      TEXT NOT NULL,
  target_entity_id UUID,
  polarity         TEXT NOT NULL CHECK (polarity IN ('assert', 'deny')),
  PRIMARY KEY (statement_key, position)
);
CREATE INDEX IF NOT EXISTS ix_wcs_wiki_references_target
  ON wcs_wiki_references (target_slug);

-- Consolidation: one decision per entity.
CREATE TABLE IF NOT EXISTS wcs_wiki_facets (
  slug        TEXT PRIMARY KEY,
  parent_slug TEXT,
  same        BOOLEAN NOT NULL DEFAULT false,
  reason      TEXT NOT NULL DEFAULT '',
  version     TEXT NOT NULL,
  model       TEXT NOT NULL DEFAULT '',
  decided_at  DATE,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Generation: each entity's current prose and watermark, and its history.
CREATE TABLE IF NOT EXISTS wcs_wiki_prose (
  slug              TEXT PRIMARY KEY,
  entity_id         UUID NOT NULL,
  generator_version TEXT NOT NULL,
  model             TEXT NOT NULL DEFAULT '',
  generated_at      DATE,
  definition        TEXT NOT NULL DEFAULT '',
  context           TEXT NOT NULL DEFAULT '',
  integrated        TEXT[] NOT NULL DEFAULT '{}',
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wcs_wiki_prose_revisions (
  slug       TEXT NOT NULL REFERENCES wcs_wiki_prose (slug) ON DELETE CASCADE,
  position   INTEGER NOT NULL,
  author     TEXT NOT NULL CHECK (author IN ('llm', 'page')),
  at         DATE NOT NULL,
  model      TEXT,
  statements INTEGER,
  definition TEXT,
  context    TEXT,
  prose      TEXT,
  PRIMARY KEY (slug, position)
);

-- The curator writes its state with a scope of its own: reading the corpus
-- (corpus-reader) does not imply rewriting the wiki, and nothing else needs
-- to.
INSERT INTO identity_roles (name, description) VALUES
  ('wiki-writer', 'Machine that writes the wiki curator''s state.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('wiki-writer', 'wcs.wiki.write')
ON CONFLICT (role_name, scope) DO NOTHING;
