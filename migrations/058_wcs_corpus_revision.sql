-- Migration 058: the corpus revision, so the wiki export is built once per change
--
-- GET /v1/wcs/wiki/export builds the whole corpus into one response, about
-- 30 MB and 15-20 s, and every curator job reads it. On 2026-10-18 five jobs
-- read it at once, each building its own copy, and the API stopped answering
-- anything else. The export is now built once per revision of the corpus
-- and kept in memory; a caller that already has that revision gets a 304.
--
-- The revision has to change on every write that changes the export, from
-- any path — routes, scripts, a hand-run fix — and several of the tables
-- have no updated_at, and rows get deleted. So triggers keep it, on every
-- table the export reads.
--
-- The bump is deferred to commit and happens once per transaction. A
-- reader therefore never sees a revision whose data it cannot see yet, and
-- the counter row is locked only for the moment a writer commits, never
-- while it is still taking other locks, so it cannot join a deadlock.
-- TRUNCATE has no row trigger; a statement trigger bumps it straight away.
--
-- The export's ETag is the revision and the row's xmin together. xmin is
-- the bumping transaction's id, so a counter recreated from zero (a test
-- reset truncates every table) never repeats an ETag an earlier row had.

CREATE TABLE IF NOT EXISTS wcs_corpus_revision (
  id         BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
  revision   BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO wcs_corpus_revision (id) VALUES (true) ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION wcs_corpus_bump() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Once per transaction: every later firing in it finds the flag set.
  IF current_setting('wcs.corpus_bumped', true) IS DISTINCT FROM 'on' THEN
    PERFORM set_config('wcs.corpus_bumped', 'on', true);
    INSERT INTO wcs_corpus_revision (id, revision, updated_at)
    VALUES (true, 1, now())
    ON CONFLICT (id) DO UPDATE
      SET revision = wcs_corpus_revision.revision + 1, updated_at = now();
  END IF;
  RETURN NULL;
END
$$;

DO $$
DECLARE
  t TEXT;
BEGIN
  -- Every table export_wiki_corpus reads.
  FOREACH t IN ARRAY ARRAY[
    'wcs_entities',
    'wcs_entity_aliases',
    'wcs_instructors',
    'wcs_instructor_aliases',
    'wcs_sources',
    'wcs_source_attributions',
    'wcs_entity_definitions',
    'wcs_entity_relations',
    'wcs_drill_purposes',
    'wcs_technique_requirements',
    'wcs_source_references'
  ] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS wcs_corpus_bump_rows ON %I', t);
    EXECUTE format(
      'CREATE CONSTRAINT TRIGGER wcs_corpus_bump_rows '
      'AFTER INSERT OR UPDATE OR DELETE ON %I '
      'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW '
      'EXECUTE FUNCTION wcs_corpus_bump()',
      t
    );
    EXECUTE format('DROP TRIGGER IF EXISTS wcs_corpus_bump_truncate ON %I', t);
    EXECUTE format(
      'CREATE TRIGGER wcs_corpus_bump_truncate AFTER TRUNCATE ON %I '
      'FOR EACH STATEMENT EXECUTE FUNCTION wcs_corpus_bump()',
      t
    );
  END LOOP;
END
$$;
