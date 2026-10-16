# 0005. Apply migrations automatically on deploy

Date: 2026-05-27

## Status

Accepted. Supersedes the operational mechanism described in ADR-0001's
"Decision" section (migrations applied "out-of-band via Railway's deploy
hook"). The substance of ADR-0001 — raw numbered SQL migrations, hand-
maintained alongside SQLAlchemy models, no Alembic — remains in force.

> **Amended (Oct 2026).** The Consequences note that the test suite runs
> on SQLite and can only exercise this runner through mocks no longer
> holds. Tests run against real Postgres (TEST-015; see
> `tests/integration/conftest.py` and the `postgres` service in
> `.github/workflows/ci.yml`), and the integration suite builds its
> schema from `migrations/` with this runner, so every CI run validates
> it against a real database.

## Context

ADR-0001 chose raw numbered SQL migration files over Alembic and stated
that migrations would be applied via Railway's deploy hook. In practice,
the deploy hook was never wired up. Migrations 001 through 018 were
applied manually — each one paste-into-the-Railway-web-UI or psql-
against-the-public-URL. Migration 019 sat unrun for over a week because
the Railway query interface auto-appends a `LIMIT` clause that breaks
`DO`/`ALTER`/`CREATE` statements, and there was no streamlined
alternative.

The manual model has two compounding problems:

1. **No record of what's been applied.** There is no `schema_migrations`
   table or equivalent. The author remembers which files have been run.
   This worked for 19 migrations and one developer; it does not scale
   even modestly.
2. **Friction creates drift.** Each manual application is enough work
   that small schema changes get batched up, deferred, or skipped, and
   the schema gets fixed up "later." Migration 019 demonstrated the
   bad version of this: the architectural design landed, the code that
   depends on the new schema landed, the system was unusable for a week
   because nothing was actively pushing the migration through.

The fix is to run migrations automatically as part of every deploy, with
a tracking table that records what has been applied.

## Decision

Add `scripts/apply_migrations.py`. The script:

1. Connects to the database via `DATABASE_URL_MIGRATIONS`, falling back to
   `DATABASE_URL` when it is unset (OPS-002).
2. Acquires a Postgres advisory lock (`pg_advisory_lock(728304917)`) so
   concurrent deploys serialize cleanly.
3. Ensures a `schema_migrations(filename TEXT PRIMARY KEY, applied_at
   TIMESTAMPTZ, duration_ms INTEGER)` table exists.
4. Lists every `migrations/NNN_*.sql` file in numeric order.
5. Filters to those whose filename is NOT in `schema_migrations`.
6. Applies each pending migration inside a transaction by default. The
   migration's body and the corresponding `INSERT INTO schema_migrations`
   row are committed atomically: if the body fails, the tracking row is
   not written, so the next deploy retries from the same point.
7. Releases the advisory lock and exits.

Migrations that cannot run inside a transaction (e.g. `CREATE INDEX
CONCURRENTLY`) opt out via a magic comment on the file's first line:

    -- migration:notransaction
    CREATE INDEX CONCURRENTLY ix_foo_bar ON foo (bar);

When the marker is present, the body runs outside a transaction and the
tracking row is written in a separate explicit transaction afterward.
No existing migrations use this; the convention exists for future needs.

The runner is wired into the image's `CMD` in the root `Dockerfile`
(originally Railway's `startCommand` in `railway.json`, since removed):

    python scripts/apply_migrations.py && exec uvicorn ...

Migrations therefore run on every deploy, before the API process starts.
If they fail, the API does not start, the deploy is marked failed in
Railway, and the problem is visible immediately. If they succeed (or
no-op), uvicorn boots normally.

### Bootstrap mode

The first deploy that ships this runner will see an empty
`schema_migrations` table and an existing production schema already at
migration 018. To avoid re-running every prior migration (which would
likely succeed by virtue of their idempotency but is unnecessary and
slow), the runner supports a one-time bootstrap.

Setting `BOOTSTRAP_MIGRATIONS=true` on a deploy causes the runner to
mark all migrations EXCEPT those named in `BOOTSTRAP_EXCLUDE` as already
applied, without running them. The default exclusion list is
`["019_wcs_entity_substrate.sql"]` because that is the only migration
that has never been applied to production at the time this runner is
introduced.

The expected first-deploy sequence is:

1. Deploy with `BOOTSTRAP_MIGRATIONS=true` set on the service.
2. The runner creates the `schema_migrations` table, marks migrations
   001 through 018 as applied, and applies migration 019 for the first
   time as a normal migration.
3. Remove `BOOTSTRAP_MIGRATIONS` from the service's environment.
4. All future deploys run as normal: any new migration files are picked
   up automatically.

### Manual escape hatches

To re-run a migration:

    DELETE FROM schema_migrations WHERE filename = 'NNN_foo.sql';

The next deploy will see it as pending and apply it again. This is rare
but useful for cases where a migration is somehow reverted manually or
where a database is restored from a backup.

To bootstrap against a different set of pre-applied migrations:

    BOOTSTRAP_MIGRATIONS=true
    BOOTSTRAP_EXCLUDE=019_foo.sql,020_bar.sql

The runner marks everything else as applied and runs only the listed
files normally.

## Consequences

**Easier:**

- Schema changes ship with their code. A pull request that adds
  migration 020 and SQLAlchemy models matching it is fully self-
  contained; the deploy applies the migration and starts the new code
  in one step.
- Recovery from outages is faster: the deploy script handles migration
  state, so a recovered database is brought up to schema head as soon as
  the API redeploys.
- The `schema_migrations` table provides a clear, queryable audit log
  of every migration ever applied to a given environment, including
  timestamps and durations.

**Harder / costs:**

- The startCommand is no longer a single uvicorn invocation. A reader
  who looks at `railway.json` will see a chained shell command and may
  initially be confused about where the migration step is happening.
  Documented here and in the runner's docstring.
- Migration failures block deploys. This is by design — a code change
  that depends on a new column should not run against a database that
  doesn't have the column — but it raises the cost of bad migrations.
  Mitigated by per-migration transactions: a failed migration is rolled
  back fully, so the database stays in a consistent state, and the fix
  is to amend the migration file and redeploy.
- The runner is asyncpg-specific. The test suite, which uses SQLite,
  cannot fully exercise the runner against a real database. The runner's
  pure-Python parts (file discovery, transactional detection, URL
  normalization, bootstrap-exclude parsing) are unit-tested; the
  asyncpg interaction is verified via mocks. Real Postgres validation
  happens on the first non-bootstrap deploy.

**Cost of this decision against standards:**

- The API-011 standard expects an Alembic invocation in CI. ADR-0001
  already documents that exemption with a different rationale. The
  exemption stands; this ADR does not change Alembic vs raw SQL, only
  the application mechanism. The exemption rationale in `evaluator.yaml`
  should reference both ADR-0001 and this ADR.

## Alternatives considered

**FastAPI lifespan hook.** Running migrations from inside the API's
startup hook would have been simpler in some ways (no shell pipe in
`railway.json`) but worse in others: it would re-run on every worker
process start in a multi-worker deployment (wasteful even though
idempotent), and it conflates "code starting up" with "schema changing,"
making the logs harder to read. Pre-start command keeps the two phases
distinct.

**Separate Railway "migrate" service.** A dedicated service whose only
job is to run migrations, triggered before deploying the API. This is
the standard pattern for larger teams but is overkill for a solo
developer: it doubles the deploy steps, requires manual coordination,
and introduces a synchronization problem between "schema is current"
and "code that depends on the schema is running."

**Alembic.** Reconsidered briefly. The objections from ADR-0001 still
apply: autogenerated migrations from SQLAlchemy diffs are unreliable
for the Postgres-specific features this schema uses (TEXT[], partial
indexes, JSONB defaults, GIN indexes, custom FK cascade behaviors).
Hand-written raw SQL is still the right choice; the gap was the
application mechanism, which this ADR fills.

**Yoyo.** A migration tool that operates on raw SQL files, similar to
what is built here. Adds a dependency and a tool to learn for what is
ultimately a 250-line script. Rejected in favor of the in-house runner,
which is small enough to read and reason about completely.
