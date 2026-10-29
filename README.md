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
- `/v1/evaluations/runs`, `/v1/evaluations/introspection`, `/v1/evaluations/fleet` — enqueue evaluator-cog jobs (SQS)
- `/v1/deejay/runs`, `/v1/transcription/runs` — enqueue deejay-cog / transcription-cog jobs (SQS)
- `/v1/tasks`, `/v1/tasks/updates`, `/v1/tasks/sweeps` — enqueue a task request / a comment and move to Review on a task the caller asked for / a sweep of the board for task-manager-cog (SQS); `/v1/tasks/groomings/*`, `/v1/tasks/agent-runs/*` — its grooming and agent-run records; `GET /v1/tasks/groomings/return-rate?days=30` — how often a task moved to Ready came back out (returned ÷ moved over the window, counted from migration 083 at the earliest); `POST /v1/tasks/groomings/budget-spent` — task-groomer-cog reports a run the spent budget stopped, served on each grooming record as `budget_spent_fingerprint` and cleared by a won claim for the same content
- `/v1/secrets/rotation-events` — the record of secret rotations: rotator-cog (and the owner's hand-run rotations) write one event per rotation; `latest=true` reads the newest per secret and config. No event carries a secret value
- `/v1/secrets/changes` — secret names Doppler reported changed, per config, oldest first; the rotator's sweep compares them with the rotation record. Names only
- `/v1/secrets/rotations` — ask rotator-cog to rotate one secret now (SQS, rotator-jobs.fifo; admin only, production only, with `config` naming the Doppler config)
- `/v1/secrets/rotation-incidents` — declare an incident: rotate every secret in the rotator's inventory, compromised, under one incident id (admin only, production only; `incident_id` resumes one). The rotator registers its entries (`/{id}/entries`) and marks their tasks (`/{id}/task-requested`); `GET /{id}` shows each secret as rotated, failed, task-opened or outstanding. Names only
- `/v1/secrets/key-use` — when a machine last proved itself with its previous key, from the audit trail's key slot; rotator-cog retires a previous key once that is a day old
- `/v1/standards/catalog`, `/v1/standards/versions` — published standards catalog
- `/v1/github/status` — public repository status board
- `/v1/identity/whoami` — what verify and resolve saw for the caller
- `/v1/flags`, `/v1/flags/{name}` — feature flags
- `/v1/stats/overview`, `/v1/stats/by-year`, `/v1/stats/top-artists`, `/v1/stats/top-tracks` — aggregate stats
- `/v1/stats/summaries`, `/v1/stats/summaries/{year}`, `/v1/stats/summaries/{year}/songs` — annual DJ summaries and each year's full song list, computed from the year's sets (the current year updates as sets land)
- `/v1/stats/collection` — the complete collection: every song on record, versions merged, marked with the years it was played
- `/v1/spotify/playlists` — Spotify playlist catalog
- `/v1/spotify/authorize`, `/v1/spotify/callback` — deejay-cog Spotify token re-authorisation
- `/v1/live-plays`, `/v1/live-plays/recent` — VirtualDJ live play history
- `/v1/ingest` — set ingestion endpoint
- `/v1/webhooks/github` — GitHub org webhook; filters deliveries by event, action and branch before forwarding to Discord (signature-verified)
- `/v1/webhooks/doppler` — Doppler `config.secrets.update` webhook; stores the changed secret names, never values (signature-verified)
- `/v1/notify` — ad-hoc Discord notifications for cogs and scripts
- `/v1/contact` — public contact form (CORS + Turnstile gated)
- `/v1/resume` — resume PDF proxy (Google Drive)
- `/v1/wcs/transcripts`, `/v1/wcs/notes`, `/v1/wcs/notes/all`, `/v1/wcs/notes/{id}` — WCS notes pipeline (legacy)
- `/v1/wcs/sources` — ingest WCS lesson extractions
- `/v1/wcs/ask`, `/v1/wcs/embeddings/refresh` — WCS Q&A agent (`docs/wcs-qa-agent.md`)
- `/v1/wcs/wiki/concepts|techniques|patterns|drills`, `…/{slug}` — entity views
- `/v1/wcs/wiki/instructors`, `/v1/wcs/wiki/instructors/{slug}` — instructor views
- `/v1/wcs/wiki/sources`, `/v1/wcs/wiki/sources/{id}`, `/v1/wcs/wiki/admin/sources[/{id}]` — source views
- `/v1/wcs/wiki/pages`, `/v1/wcs/wiki/pages/{slug}`, `/v1/wcs/wiki/admin/pages[/{slug}]` — the stored wiki pages wiki-curator-cog builds (it writes them; the site reads them)
- `/v1/wcs/wiki/state/links|facets|prose|thin` — wiki-curator-cog's build state between passes (cog-only)
- `/v1/wcs/wiki/jobs` — put wiki jobs on the curator's queue (cog-only); `/v1/wcs/wiki/admin/jobs` — start a free wiki operation (admin)
- `/v1/wcs/wiki/admin/revisions`, `…/revisions/sources`, `…/revisions/{id}` — page revision history (admin)
- `/v1/wcs/wiki/admin/notes` — send a note written on the site (admin)
- `/v1/wcs/wiki/export` — bulk corpus export (for wiki-curator-cog); built once per corpus revision, ETag/304, 503 + `Retry-After` when busy
- `/v1/wcs/me`, `/v1/wcs/admin/users[/{id}]`, `/v1/wcs/admin/grants[/{id}]`, `/v1/wcs/admin/notes/{id}`, `/v1/wcs/admin/notes/{id}/visibility` — WCS access control and note metadata
- `/v1/wcs/admin/sources/{id}`, `/v1/wcs/admin/sources/{id}/visibility` — direct edits of a source's metadata and visibility
- `/v1/wcs/admin/corrections/*`, `/v1/wcs/admin/additions/*` — input-layer overrides
- `/v1/wcs/admin/recompose/{source_id}` — manual composition trigger
- `/v1/wcs/admin/gaps/*` — curation gap-finding
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

# 3. Secrets come from this repo's own Doppler dev config — nothing reads a .env
#    file, and local runs never use prd. Once per machine, then per clone:
brew install gnupg dopplerhq/cli/doppler && doppler login
doppler setup                 # reads doppler.yaml: mini-app-polis-ecosystem / dev
uv run check-doppler-keys     # every required .env.example name is in dev
```

### Run the server
`DATABASE_URL` is not in Doppler; set it in the shell for your local
Postgres. `doppler run` passes it through because Doppler holds no value
for that name; if it did, Doppler's would win:
```bash
export DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/deejay_sets
doppler run -- uv run uvicorn src.kaianolevine_api.main:app --reload
```

API docs available at http://localhost:8000/docs

### Run tests
```bash
# Unit tests — no database needed
uv run pytest tests/unit

# All tests — integration tests need a local Postgres (with pgvector)
# and a database whose name ends in _test; its schema is rebuilt from
# migrations/ on every run.
createdb kaianolevine_test
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kaianolevine_test uv run pytest

# With coverage detail
uv run pytest --cov=src --cov-report=term-missing

# In parallel, as CI runs it: each pytest-xdist worker builds its own
# database beside TEST_DATABASE_URL's (kaianolevine_gw0_test, ...), so the
# role needs CREATEDB
uv run pytest -n auto
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

Hooks run on every `git commit`: ruff lint, ruff format, gitleaks. If a hook fails the commit is blocked — ruff will auto-fix in place, then `git add .` and re-commit.

## CI/CD

Every push and pull request to `main` or `dev` runs CI (security scan, lint, type check, tests).
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

Designed for Railway. The image is the root Dockerfile, whose CMD applies
migrations and starts uvicorn. The service's settings — builder,
healthcheck, restart policy, limits, and the variables it reads — are
declared in `.railway/railway.ts` and applied to production by CI, as infra
applies Terraform: the `dev` → `main` pull request is planned and, once
merged, applied — removals only with the `allow-destroy` label. Development
keeps its dashboard settings (`.railway/README.md`).

## Authentication and authorization

This service is a conformant enforcement point for the `identity`
contract. It binds four functions — verify, resolve, authorize,
emit_audit — once per request, in `src/kaianolevine_api/auth.py`. The
decision itself lives in `identity.policy`; what stays here is
configuration and the FastAPI adapters.

### Credentials

One `Authorization: Bearer <credential>` header carries either of two
credential types, routed structurally by dot count:

- **Clerk session JWTs** (people) — two dots. RS256, verified in process
  against the issuer's JWKS document. The `sub` claim is the principal
  subject.
- **Named machine keys** (cogs) — anything else. An opaque per-machine
  string, compared in constant time against the keys this service holds
  in its own environment. The matched key *names* the caller, so no
  machine name is ever asserted in a request. Verification is local:
  nothing leaves the process.

There is no fallback between the two paths. A credential that fails the
path it routed to is rejected, not retried against the other. Clerk M2M
opaque tokens and `CLERK_SECRET_KEY` were removed outright — see
ecosystem-standards ADR-0008 and CD-019.

Required environment variables:

- `CLERK_ISSUER` and `CLERK_JWKS_URL` — or `CLERK_ISSUERS`, a JSON array
  of `{issuer, jwks_url}`, when more than one Clerk tenant is trusted.
- `<MACHINE_NAME>_API_KEY` — one per machine declared in
  `identity_registry.MACHINES` (`deejay-cog` → `DEEJAY_COG_API_KEY`).
  Doppler-managed. Key material is never stored in the database, hashed
  or otherwise; the principal store holds names and grants only.
  `<MACHINE_NAME>_API_KEY_PREVIOUS`, optional, is the key being rotated
  out: either key proves the machine while a rotation overlaps, and each
  audit event records which one matched (`key_slot`), so the old key is
  retired once nothing presents it.
- `OWNER_CONSOLE_API_KEY` — optional; the `owner-console` machine's key,
  used by the owner's local scripts (rotator-cog's `rotate.py`). It holds
  only the `secrets-rotator` role: read and record rotation events.

Every variable the service reads, auth's included, is listed with whether it
is required in [`.env.example`](.env.example); defaults are in
`src/kaianolevine_api/config.py`.

Machine principals and their roles are declared in code and reconciled
into the store at boot. Adding a cog is a reviewed code change, not a
row inserted by hand.

### Route coverage

Every route is in exactly one of three states:

- **Scope-guarded** — `Depends(require_scope("<domain>.<resource>.<action>"))`.
  Roles are named bundles of scopes; a suspended principal is denied
  before roles are consulted.
- **Authenticated-only** — a verified credential, no scope. This is
  the whole list: `POST /v1/wcs/me` (where a person first becomes known,
  so it cannot require a principal in order to grant one), `GET /v1/wcs/me`
  (reads only the caller's own profile), and `GET /v1/identity/whoami`
  (reports what verify and resolve saw, and authorizes nothing).
- **Public** — see below. Each one's OpenAPI description says why it is intentionally public (DOC-011).

Every decision is audited, allow and deny alike, with the enforcement
point, principal, scope, outcome and reason.

### Public surface

Deliberately unauthenticated, per API-008 / DOC-011:

| Routes | Why |
|---|---|
| `GET /v1/sets`, `/v1/sets/{id}`, `/v1/sets/{id}/tracks`, `/v1/tracks`, `/v1/tracks/{id}`, `/v1/catalog`, `/v1/catalog/{id}` | Public catalog reads backing the website. |
| `GET /v1/stats/overview`, `/v1/stats/by-year`, `/v1/stats/top-artists`, `/v1/stats/top-tracks`, `/v1/stats/summaries`, `/v1/stats/summaries/{year}`, `/v1/stats/summaries/{year}/songs`, `/v1/stats/collection`, `/v1/spotify/playlists`, `/v1/live-plays/recent` | Public aggregate reads backing the website. |
| `GET /v1/evaluations`, `/v1/evaluations/summary`, `/v1/flags` | Pipeline Health and feature flags — read-only, no per-owner content. |
| `POST /v1/contact` | Contact form. CORS + Turnstile gated rather than credential gated. |
| `GET /v1/resume` | Public resume proxy. |
| `GET /v1/github/status` | Public repo status board; same response for every caller, only what the committed config permits. |
| `GET /v1/standards/catalog`, `/v1/standards/versions` | Standards rule text, already public; reads stay off the credential path every evaluation depends on. |
| `GET /v1/spotify/authorize`, `/v1/spotify/callback` | Browser re-auth flow; the callback refuses any Spotify account but `SPOTIFY_OWNER_USER_ID`. |
| `POST /v1/webhooks/github` | GitHub org webhook. Gated by `X-Hub-Signature-256` over the raw body — the only credential GitHub can present — not by a bearer token. Signed with `GITHUB_WEBHOOK_SECRET`, or `GITHUB_WEBHOOK_SECRET_PREVIOUS` while a rotation overlaps. |
| `POST /v1/webhooks/doppler` | Doppler's secrets.update webhook. Gated by `X-Doppler-Signature` over the raw body, signed with `DOPPLER_WEBHOOK_SECRET`, or `DOPPLER_WEBHOOK_SECRET_PREVIOUS` while a rotation overlaps. Stores names only. |
| `GET /health`, `GET /version`, `GET /` | Platform endpoints. `/` redirects to `/docs`. |

Client parity is maintained with `mini_app_polis.api.KaianoApiClient`,
which derives the caller's own key variable from its machine name and
sets the same header this module reads. The two must change in the same
release (AUTH-002) — a mismatch 401s every cog at once.

## Observability

Three-layer observability, aligned with the ecosystem standard:

- **Sentry** — unhandled exceptions and FastAPI integration, initialized
  in `main.py` `lifespan` when `SENTRY_DSN_APIS` is set.
- **Structured logs** — emitted via the shared logger from
  `mini_app_polis.logger` (install name `common-python-utils`); consistent
  JSON format and emoji-prefixed lifecycle lines across the ecosystem.
- **Healthchecks.io** — external uptime probes hit `/health` (public
  liveness endpoint, no auth, no DB access).

### Notifications

Every producer reaches Discord through `services.discord`, the only
module that calls it. GitHub payloads go to a webhook URL's `/github` suffix so
Discord renders its own embed from the bytes GitHub signed; everything
else goes to the bare URL as an ordinary message.

Messages are routed to one of four channels, chosen at the call site
because that is the last place that knows what a message means:

| Channel | What lands there |
|---|---|
| `errors` | failed CI on the default branch, `/v1/notify` run reports declaring WARN or worse (copied from `runs`), jobs dropped before reaching a queue, the last Spotify token-expiry reminder, this service's own 5xx and machine-facing 4xx, and a failed identity reconcile at boot |
| `activity` | the running list of committed data changes, from the request middleware |
| `runs` | cog run reports through `/v1/notify`, every severity |
| `default` | everything else — pushes, pull requests, issues, releases, green CI |

Each channel has its own webhook variable — `DISCORD_WEBHOOK_URL_DEFAULT`,
`DISCORD_WEBHOOK_URL_ERRORS`, `DISCORD_WEBHOOK_URL_ACTIVITY`,
`DISCORD_WEBHOOK_URL_RUNS` — and each falls back to `DISCORD_WEBHOOK_URL`
when unset. Separate variables rather than one map so each rotates on its
own and reads at a glance in Doppler.

The fallback is the rollout strategy: channels are configured one at a
time, and a channel that exists in code but not yet in Discord delivers to
the original webhook instead of vanishing. The corollary is that an unset
variable is a silent no-op that looks exactly like an unsplit channel, so
every send logs `channel=` and the producer that chose it.

A cog's own reports all go to `runs` so that channel is a complete
record of the fleet's runs; a report whose footer declares WARN, ERROR or
CRITICAL is also copied to `errors`. A run that died too hard to report
itself is caught by its own cog's backstop where it has one — a queue-fed
Lambda cog's dead-letter-queue and queue-stalled alarms, or watcher-cog's
failing-ticks alarm and Healthchecks check — not by this service.

The GitHub route exists because GitHub's webhooks filter by event type and
nothing else. "Pushes to main", "new pull requests" and "releases, but not
edits to old ones" are all decisions that can only be made after the
payload arrives, so they are made here. One org-level webhook per org
points at this route; the policy is one function per event type in
`routers.notifications`:

| Event | Forwarded when |
|---|---|
| `push` | ref is the repo's default branch, not a deletion, and the head commit is not a merge or squash commit |
| `pull_request` | action is `opened` or `closed` |
| `issues` | action is `opened` |
| `release` | action is `published` |
| `workflow_run` | run completed on the default branch — every conclusion, pass or fail; `failure`, `timed_out` and `action_required` route to `errors`, the rest to `default` |

Two delivery shapes, because Discord's GitHub endpoint is not universal.
`push`, `pull_request`, `issues` and `release` are forwarded byte-for-byte
to the webhook's `/github` suffix and Discord draws its own embed.
`workflow_run` is not: Discord accepts it, answers 204 and posts nothing,
a silence indistinguishable from success at every layer above it. That
embed is built in `build_workflow_message` and posted to the bare webhook
URL instead.

Everything dropped still answers 200 with the reason in the body — a drop
is a decision, not an error GitHub should retry, and the delivery log
reads better when the only red entries are real ones. A delivery Discord
rejected also answers 200: GitHub disables a webhook that collects enough
5xx, so failures go to Sentry instead of onto the wire.

Two known gaps, both deliberate. Workflow runs off the default branch are
dropped, so a pull request whose CI fails is silent until it merges. And
rebase-and-merge cannot be told apart from a direct push, so a rebase
merge is announced twice — once by the PR closing, once by the push.

`/v1/notify` posts to the `runs` channel and requires
`notify.messages.send`, carried by the `notifier` role. Every fleet machine holds it (`owner-console`, the key for the owner's local scripts, is the one exception): the scope posts a message and does
nothing else, and a cog that cannot report its own failure reports it
later than it should. `ops-notifier` exists for notifications that belong
to no cog — scripts, one-offs, GitHub Actions — so those never have to
borrow a cog's key.

`GITHUB_NOTIFY_EVENTS` is the outer gate — the event types this service
has a policy for at all — and defaults to the five in the table.
`check_run`, `check_suite` and `status` are absent on purpose: they
duplicate `workflow_run`, and Discord renders none of the three.

`DISCORD_NOTIFY_EVENTS` decides which of the running list's messages each
environment posts. It maps an environment name (`ENVIRONMENT`) to the
events that environment sends, as JSON in a variable:

| Event | Message |
|---|---|
| `data_changed` | the summary of what a request committed, to `activity` |
| `fault` | this service's own 5xx and machine-facing 4xx, to `errors` |

An event left out of an environment's list is not sent, and an environment
with no entry sends every event. The defaults (in `config.py`) send both
in production and only `fault` in development, where the writes are
contract-suite runs and test cleanup — but neither event posts outside
production whatever the list says (common-python-utils 7), so
development's entry has no effect. The `NOTIFY_DATA_CHANGES`,
`NOTIFY_FAULTS` and `NOTIFY_MACHINE_CLIENT_ERRORS` switches still apply on
top. It decides what reaches Discord only: run reports in `runs` and
`errors`, and the signals detectors, are not affected. Unsetting a
channel's webhook is not a way to silence it — the channel falls back to
`DISCORD_WEBHOOK_URL`.

### Signals: detected problems that may become tasks

The GitHub webhook, `POST /v1/notify` and this service's own 5xx answers
feed `services/signals.py`, independently of what is announced on Discord. A
detector turns an event into a signal (a kind, a key naming the problem,
failing or recovered); a streak holds one problem from its
first failure until it recovers; and a streak becomes a task request for
task-manager-cog only when its kind's rule says so. A streak that heals first
closes quietly and is only logged. Production only.

| Kind | Key | Before a task | Closes on |
|------|-----|---------------|-----------|
| `ci` | `<repo>/<workflow>@<branch>`, branches `SIGNAL_CI_BRANCHES` (`main` only: a dev failure makes no task) | one remedy, chosen by the failed jobs: a failed dependency audit dispatches the org's `dependency-fix` workflow for the repo, anything else is re-run. The next failure escalates | a green run |
| `cog-run` | `<repo>/<flow>`, from run reports | none: the next run is the retry. `SIGNAL_RUN_ESCALATE_AFTER` (2) failing runs (`ERROR` or `CRITICAL`) escalate | a run reported at `SUCCESS`, `INFO` or `WARN`. An idle run is not reported, so it does not close a streak |
| `api-fault` | `<method> <route template> <status>`, from the activity middleware | none. `SIGNAL_FAULT_ESCALATE_AFTER` (3) 5xx answers, each within `SIGNAL_FAULT_WINDOW_MINUTES` (30) of the last, escalate | a quiet window: the next 5xx after it starts a new streak. Paths in `NOTIFY_EXCLUDED_PATHS` are not watched |

A remediation that cannot start (no `GITHUB_ACTIONS_TOKEN`, GitHub refuses)
escalates at once. The task's external id is `<kind>.<key>#<streak id>`, so a
streak makes one task however many failures it sees.

A recovered streak's task is commented and moved to Review: when an escalated
streak closes, the API sends task-manager-cog a `task.update` for its task. A
streak closed by its recovery event names that event and links it where the
event has a link (a green run's page; a run report has none, so the run is
named); one closed by its quiet window says the window passed without another
fault. Nothing marks the task complete; a person confirms the fix and closes
it. A streak that healed before escalating has no task and sends nothing. An
update that does not land is reported, like an escalation, and the recovery
stands.

The task arrives ready. Each detector fills the template's `Done when` (the
check passing again, the flow finishing without an error, the route no longer
answering the status) and `Constraints` (fix the cause; do not silence the
check), and every escalated task is sized `S`. So it passes task-manager-cog's
definition of ready on arrival and needs no grooming run.
