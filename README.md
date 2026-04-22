# kaianolevine-api

FastAPI service powering api.kaianolevine.com — sets, tracks, catalog
reconciliation, pipeline evaluations, feature flags, stats, live plays,
contact form handling (Brevo + Turnstile), and resume PDF proxy
(Google Drive).

## API Reference

All versioned routes are mounted under `/v1`. Interactive OpenAPI
documentation is the source of truth for request and response shapes:

- Live (production): https://api.kaianolevine.com/docs
- OpenAPI JSON:      https://api.kaianolevine.com/openapi.json
- Local (dev):       http://localhost:8000/docs

Route groups:

- `/v1/sets`, `/v1/sets/{id}`, `/v1/sets/{id}/tracks` — DJ sets and per-set tracks
- `/v1/tracks`, `/v1/tracks/{id}` — track catalog
- `/v1/catalog`, `/v1/catalog/{id}` — reconciled catalog entries
- `/v1/evaluations`, `/v1/evaluations/summary` — pipeline evaluation findings
- `/v1/flags`, `/v1/flags/{name}` — feature flags
- `/v1/stats/overview`, `/v1/stats/by-year`, `/v1/stats/top-artists`, `/v1/stats/top-tracks` — aggregate stats
- `/v1/spotify/playlists` — Spotify playlist catalog
- `/v1/live-plays`, `/v1/live-plays/recent` — VirtualDJ live play history
- `/v1/ingest` — set ingestion endpoint
- `/v1/prefect-webhook` — Prefect flow-run webhook
- `/v1/contact` — public contact form (CORS + Turnstile gated)
- `/v1/resume` — resume PDF proxy (Google Drive)
- `/v1/wcs/transcripts`, `/v1/wcs/notes`, `/v1/wcs/notes/all`, `/v1/wcs/notes/{id}` — WCS notes pipeline
- `/v1/wcs/me`, `/v1/wcs/admin/users`, `/v1/wcs/admin/grants`, `/v1/wcs/admin/notes/{id}/visibility` — WCS access control
- Unversioned meta routes: `/health` (liveness), `/version` (deployed version), `/` (redirects to `/docs`)

## Developer Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
- PostgreSQL (or use the `DATABASE_URL` from Railway for a shared dev DB)

### First-time setup
```bash
# 1. Install all dependencies including dev extras
uv sync --all-extras

# 2. Install pre-commit hooks
uv run pre-commit install

# 3. Copy env file and fill in values
cp .env.example .env
```

### Run the server
```bash
uv run uvicorn src.kaianolevine_api.main:app --reload
```

API docs available at http://localhost:8000/docs

### Run tests
```bash
# All tests (uses SQLite in-memory — no DATABASE_URL needed)
uv run pytest

# With coverage detail
uv run pytest --cov=src --cov-report=term-missing
```

### Lint, format, type check
```bash
uv run ruff check src/ tests/ --fix
uv run ruff format src/ tests/
uv run mypy src/
```

### Pre-commit (runs automatically on every commit)
```bash
# Run manually against all files
uv run pre-commit run --all-files
```

Hooks run on every `git commit`: ruff lint, ruff format, mock method checks, type annotation checks. If a hook fails the commit is blocked — ruff will auto-fix in place, then `git add .` and re-commit.

## CI/CD

Every push to `main` runs CI (lint + tests).
Railway auto-deploys on push to `main`.
Feature flags control activation without deployment.
Flags are managed via `PATCH /v1/flags/{name}`.

## Versioning

This repo uses semantic-release for automated versioning.
Versions are determined automatically from commit messages
on merge to main:

- feat: → minor version bump (0.3.1 → 0.4.0)
- fix: → patch version bump (0.3.1 → 0.3.2)
- feat!: or BREAKING CHANGE → major bump (0.3.1 → 1.0.0)
- chore/docs/refactor/test/ci → no version bump

Never manually edit the version in pyproject.toml.
Never manually edit CHANGELOG.md.
Both are managed automatically on merge to main.

### Production Flag Rollback

Use flags for safe rollout and fast rollback without redeploying:

- Enable one flag change at a time via `PATCH /v1/flags/{name}`.
- Verify health immediately after change (API status, error logs, and endpoint behavior).
- If regressions appear, rollback by patching the same flag back to `enabled: false` (or `true` for previously disabled flags).
- Prefer changing ingest-related flags during low-traffic windows and monitor pipeline runs for 5-10 minutes after each change.
- Record each production flag flip in deployment notes (flag name, old/new value, timestamp, operator).

## Deployment Target

Designed for Railway.

## Authentication

Owner identity is resolved from a Clerk bearer token via
`src/kaianolevine_api/auth.py`. The `get_current_owner` dependency
accepts either:

- **Clerk session JWTs** (human users) — RS256, verified locally against
  the JWKS document fetched from `CLERK_JWKS_URL` (cached for 5 minutes).
- **Clerk M2M opaque tokens** (cogs) — verified via the Clerk BAPI
  `m2m_tokens/verify` endpoint using `CLERK_SECRET_KEY`.

Required environment variables:

- `CLERK_JWKS_URL` — e.g. `https://clerk.kaianolevine.com/.well-known/jwks.json`
- `CLERK_ISSUER` — e.g. `https://clerk.kaianolevine.com`
- `CLERK_SECRET_KEY` — Clerk secret key for opaque-token verification

Header parity is maintained with
`mini_app_polis.api.KaianoApiClient`: the client attaches
`Authorization: Bearer <token>` acquired from Clerk and this module
verifies tokens arriving in that same header.

## Observability

Three-layer observability, aligned with the ecosystem standard:

- **Sentry** — unhandled exceptions and FastAPI integration, initialized
  in `main.py` `lifespan` when `SENTRY_DSN_API` is set.
- **Structured logs** — emitted via the shared logger from
  `mini_app_polis.logger` (install name `common-python-utils`); consistent
  JSON format and emoji-prefixed lifecycle lines across the ecosystem.
- **Healthchecks.io** — external uptime probes hit `/health` (public
  liveness endpoint, no auth, no DB access).
