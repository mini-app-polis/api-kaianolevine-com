-- Migration 056: the wiki's page documents
--
-- wiki-curator-cog's materialise step builds one structured JSON document per
-- entity page — definition, In practice, Details, By teacher with citations,
-- Mentions, Referenced by — and posts it here (ADR-006). The page
-- logic stays in the curator; this API stores the documents, filters them by
-- the reader's access to their sources, and serves them.
--
-- One stored document per page, not a public and a private copy. Each
-- citation names its source, and a read drops what the reader cannot see,
-- going by the source's visibility now rather than when the page was built.
-- evidence_sources lists every source the page's prose was built from, so a
-- page whose evidence a reader can see none of is not served to them.
--
-- Keyed by slug, with no foreign key into wcs_entities: concepts the linker
-- proposed have pages but no entity row (as in migration 054).

CREATE TABLE IF NOT EXISTS wcs_wiki_pages (
  slug             TEXT PRIMARY KEY,
  kind             TEXT NOT NULL,
  name             TEXT NOT NULL,
  document         JSONB NOT NULL,
  evidence_sources UUID[] NOT NULL DEFAULT '{}',
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_wcs_wiki_pages_updated
  ON wcs_wiki_pages (updated_at DESC);
