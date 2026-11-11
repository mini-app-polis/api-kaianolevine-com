# DESIGN.md

## Architecture Overview

`kaianolevine-api` is a FastAPI service deployed on Railway. The production stack uses
SQLAlchemy async ORM with `asyncpg` against PostgreSQL. Integration tests run against a
local PostgreSQL `*_test` database built from `migrations/`; unit tests need no database.

Write traffic is intentionally constrained: cogs are the only intended write
clients for ingest and automation pathways. User-facing read APIs expose sets, tracks,
catalog, notes, and stats. Runtime write surfaces for ingest and live plays are gated by
feature flags so ingestion can be paused without redeploying. Sentry is enabled in API
runtime (when configured) for centralized error tracking and observability.

API documentation is served automatically by FastAPI at `/docs` (Swagger UI) and
`/redoc` (ReDoc). The root URL (`/`) redirects to `/docs`. The OpenAPI schema is
available at `/openapi.json`. All three reflect the currently deployed package version.

## API Endpoints

All endpoints return the standard success envelope:

`{ "data": ..., "meta": { "count": <n>, "total": <n>, "version": "<package version>" } }`

`meta.version` is the deployed package version, the same value `GET /version` reports.

Errors return:

`{ "error": { "code": "...", "message": "..." } }`

### Sets

* `GET /v1/sets` — list sets (`year`, `venue`, `date_from`, `date_to`, `limit=50`, `offset=0`)
* `GET /v1/sets/{id}` — single set with full track list
* `GET /v1/sets/{id}/tracks` — ordered track list for a set

### Tracks

* `GET /v1/tracks` — query tracks (`artist`, `title`, `genre`, `bpm_min`, `bpm_max`, `year`, `data_quality`, `limit=50`, `offset=0`)
* `GET /v1/tracks/{id}` — single track play with set context

### Catalog

* `GET /v1/catalog` — list catalog entries (`artist`, `title`, `confidence`, `min_play_count`, `limit`, `offset`)
* `GET /v1/catalog/{id}` — catalog entry with play history
* `PATCH /v1/catalog/{id}` — update `genre`, `bpm`, `release_year` (source becomes `manual`); protected

### Evaluations

* `GET /v1/evaluations` — list findings (`repo`, `dimension`, `severity`, `limit`, `offset`)
* `GET /v1/evaluations/summary` — aggregate by severity and dimension
* `POST /v1/evaluations` — write findings; protected

### Stats

* `GET /v1/stats/overview`
* `GET /v1/stats/by-year`
* `GET /v1/stats/top-artists`
* `GET /v1/stats/top-tracks`
* `GET /v1/stats/summaries` — headline numbers per year; the current year is `in_progress`
* `GET /v1/stats/summaries/{year}` — one year's summary, computed from its sets
* `GET /v1/stats/summaries/{year}/songs` — every song played in the year, versions merged, most played first
* `GET /v1/stats/collection` — every song on record, versions merged, most played first, with the years each was played

### Ingest

* `POST /v1/ingest` — accept a set with track list, run reconciliation, return set id + catalog stats; protected

### Queue deduplication

Routes that enqueue to a FIFO cog queue (`POST /v1/deejay/runs`, `POST /v1/transcription/runs`, wiki jobs, and the rest) send their own `MessageDeduplicationId` instead of relying on the queue's content-based deduplication. SQS's own five-minute window is fixed and cannot be changed; the explicit id takes precedence over it, so the window is set through the id.

The id hashes the message group, the message body and a time bucket of `job_queue.DEDUP_WINDOW_SECONDS` (about a minute; the one place to change it). A deejay or transcription run started through its route also carries the request's own identity in the message, `request: {id, requested_at, caller}`, so two triggers are two messages and the run can say who asked. The request's `id` and `requested_at` are left out of the deduplication id and the `caller` is kept, so:

* the same request from the same caller again within the bucket is collapsed by SQS into the first;
* a deliberate re-run in a later bucket is queued;
* a repeat straddling a bucket boundary is queued twice, so the window is "about" a minute, never longer.

A job meant to run every time it is asked for (the task-manager sweep) sends its own unique id instead and is never collapsed.

A collapsed request is answered `200` with `deduplicated: true` and the `message_id` of the earlier message. SQS gives no other sign of a collapse than answering with the kept message's id, and a new message always gets a new id, so the API remembers the ids it has been given, in process memory, for SQS's five minutes: an id it has seen before is a collapse. After a restart a collapsed request reads as new.

## Reconciliation + Normalization

Normalization and reconciliation rules are implemented in:
* `src/kaianolevine_api/services/normalization.py`
* `src/kaianolevine_api/services/reconciliation.py`

