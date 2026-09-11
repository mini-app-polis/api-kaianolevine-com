-- Migration 028: the standards catalog store, and the role that publishes it
--
-- ecosystem-standards compiles its rule files into one normalized document on
-- release and posts it here. Consumers read it from this API instead of
-- walking the standards repo — a conformance run previously fetched the index
-- and every domain file, once per repo under evaluation, and re-derived the
-- same structure each time.
--
-- A published version is immutable. EVAL-002 exists so a finding is traceable
-- to a specific standards state; that traceability is a fiction if 6.13.0 can
-- mean different things on different days. The route enforces it — re-posting
-- a version with different content is a conflict, not an update — and the
-- primary key is what makes the enforcement possible rather than advisory.
--
-- version_sort exists because "latest" means the highest version, not the most
-- recently inserted row, and Postgres cannot order `6.9.0` before `6.13.0` as
-- text. The API writes a zero-padded key alongside the version it derives it
-- from. Ordering on published_at would be correct only while releases arrive
-- in order, which is true today and is not a property worth depending on.

CREATE TABLE IF NOT EXISTS standards_catalogs (
  version       TEXT PRIMARY KEY,
  version_sort  TEXT        NOT NULL,
  compiled_at   TIMESTAMPTZ NOT NULL,
  rule_count    INTEGER     NOT NULL,
  payload       JSONB       NOT NULL,
  published_by  TEXT        NOT NULL,
  published_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_standards_catalogs_version_sort
  ON standards_catalogs (version_sort DESC);

-- Publishing the rubric is a larger capability than any single consumer of it
-- needs, so it is its own role rather than an addition to an existing bundle.
-- The key lives as a repository-level secret on ecosystem-standards, not as an
-- organisation-level one: the CI credential every repo holds is for asking
-- that a repo be evaluated, and a key that can also rewrite what "evaluated"
-- means would make that distinction meaningless.
--
-- The role is granted to the declared machine ecosystem-standards by
-- identity_registry.reconcile at boot. Nothing is granted here.

INSERT INTO identity_roles (name, description) VALUES
  ('standards-publisher', 'May publish a compiled standards catalog version.')
ON CONFLICT (name) DO NOTHING;

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('standards-publisher', 'standards.catalog.publish')
ON CONFLICT (role_name, scope) DO NOTHING;
