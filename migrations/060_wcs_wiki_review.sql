-- Migration 060: the wiki's review trail — page revisions and thin entities
--
-- /admin/wiki shows what each source changed in the wiki, and which entities
-- have too little evidence to stand on their own.
--
-- wcs_wiki_page_revisions: a page write (POST /v1/wcs/wiki/pages) records one
-- row for every page it creates, changes or removes, in the same transaction
-- as the write. A rewrite of an identical page records nothing. previous is
-- the stored document before (null when created); document is the one after
-- (null when removed). origin_source_id names the source whose link pass
-- started the work, or is null for work an admin or an operator started;
-- origin_job_id names the run. No foreign key: a revision outlives the source
-- that caused it. No retention yet — every revision is kept.
--
-- wcs_wiki_thin: the thin entities the curator finds, one document each,
-- keyed by slug. The curator replaces the list; the API stores and serves it.
--
-- Neither table is read by the wiki export, so neither carries the corpus
-- revision triggers from migration 058.

CREATE TABLE IF NOT EXISTS wcs_wiki_page_revisions (
  id               BIGSERIAL PRIMARY KEY,
  slug             TEXT NOT NULL,
  change           TEXT NOT NULL CHECK (change IN ('created', 'changed', 'removed')),
  origin_source_id UUID NULL,
  origin_job_id    TEXT NULL,
  previous         JSONB NULL,
  document         JSONB NULL,
  at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_wcs_wiki_page_revisions_origin
  ON wcs_wiki_page_revisions (origin_source_id, at DESC);
CREATE INDEX IF NOT EXISTS ix_wcs_wiki_page_revisions_slug
  ON wcs_wiki_page_revisions (slug, at DESC);
CREATE INDEX IF NOT EXISTS ix_wcs_wiki_page_revisions_at
  ON wcs_wiki_page_revisions (at DESC);

CREATE TABLE IF NOT EXISTS wcs_wiki_thin (
  slug       TEXT PRIMARY KEY,
  data       JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
