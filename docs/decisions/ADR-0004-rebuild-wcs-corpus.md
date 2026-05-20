# ADR-0004 — Rebuild WCS corpus from scratch; deprecate `wcs_notes`

**Date:** 2026-05-19
**Status:** Accepted
**Related:** ADR-0002 (entity substrate), ADR-0003 (versioned extractions)

## Context

ADR-0002 commits to a new substrate (entities in Postgres) and ADR-0003 commits to a new input layer (versioned extractions with corrections and additions). Both require migrating away from the current `wcs_notes.notes_json` blob representation.

Two migration shapes are possible:

**Parallel-run migration.** Keep `wcs_notes` working. Build the entity tables alongside. Backfill from existing `notes_json` by running entity resolution against each row. Maintain both write paths in `transcription-cog` during transition. Keep the notes UI reading from `wcs_notes` via a compatibility view. Eventually deprecate `wcs_notes` once everything is migrated and verified.

**Rebuild from scratch.** Drop `wcs_notes` and `wcs_transcripts` (or rename to `_legacy_*`). Stand up the new substrate. Re-extract every transcript through the new pipeline. Notes UI is offline for the rebuild window. No transitional code; uniform extraction quality across the entire corpus.

The decision turns on a few project-state facts:

- **Proof-of-concept stage.** No paying users, no external dependencies on uptime. The only consumer of the notes UI is the operator.
- **Transcripts preserved in Drive.** Every successfully-ingested transcript is archived to `NOTES_PROCESSED_FOLDER_ID`. The raw text is available outside the database.
- **No attachment to existing extractions.** The operator does not value any specific existing `notes_json` as a curated artifact; the corpus has value as a queryable substrate, not as a set of carefully-preserved outputs.
- **The new extraction prompt is meaningfully different.** It extracts entity kinds, relationships, drill purposes, technique requirements — structure that's not in current `notes_json`. A backfill from existing `notes_json` would produce structurally thinner data than fresh extractions would. The corpus would be two-tier (thin legacy, rich new) forever, or until re-extracted — which is the same cost as a fresh rebuild plus the migration code.

The case for parallel-run is strong when downtime is unacceptable and legacy data has preserved value. Neither applies here.

## Decision

**Rebuild the WCS corpus from scratch under the new substrate.** Drop `wcs_notes` and `wcs_transcripts` (preserved as `_legacy_*` for a transition period). Re-extract every transcript currently in `NOTES_PROCESSED_FOLDER_ID` through the new pipeline. Accept notes UI downtime during the rebuild window.

### Phasing

The rebuild is executed in four phases:

**Phase 0: Design lock and prompt iteration.**

Before any production changes:
- Migration 019 specified (entity schema, input-layer schema, indexes, FKs).
- The new extraction prompt iterated against 3-5 hand-picked representative transcripts (variety of instructors, session types, content density).
- Validation that the new prompt produces the entity structure the new schema expects.

Iteration is cheap here — single transcripts, single LLM calls, no schema commitment yet. The cost of getting the prompt wrong and discovering it post-rebuild is high; the cost of iterating now is low.

**Phase 1: API substrate lands.**

- Migration 019 runs on production. New tables exist alongside `wcs_notes` / `wcs_transcripts`.
- The Composition Service is implemented and tested against synthetic inputs (mock claims → verified entity rows).
- New endpoints land: `POST /v1/wcs/sources` (or whatever the new write path is named), `GET /v1/wcs/wiki/concepts/{slug}`, admin endpoints for corrections and additions.
- The notes UI is unaffected — still reading from `wcs_notes` via existing endpoints.

**Phase 2: `transcription-cog` cuts over.**

- `transcription-cog` deploys with the new prompt and the new write path.
- Old `POST /v1/wcs/notes` write path is removed.
- Any new Drive drops flow through the new pipeline into the new schema.
- Existing `wcs_notes` rows still exist; notes UI still reads from them. New ingests no longer add to that table.

**Phase 3: Backfill from Drive; deprecate legacy.**

- Notes UI displays a "Rebuilding" banner with an estimated completion time. (Alternatively: kept offline with a static message.)
- Tables `wcs_notes` and `wcs_transcripts` are renamed to `_legacy_wcs_notes` and `_legacy_wcs_transcripts`. They are preserved indefinitely as a fallback, with a reevaluation point at ~4 weeks (see below).
- A one-off backfill script iterates every transcript file in `NOTES_PROCESSED_FOLDER_ID`, runs each through the new pipeline (read → parse filename → call LLM → POST to new endpoint).
- Backfill validates as it goes (each extraction validated against new schema; failures logged for review).
- The wiki-curator renders fresh markdown from the populated entity store.
- Notes UI is updated to read from new substrate (via a compatibility endpoint that reconstructs `notes_json`-shaped JSON from entity rows, or a refactored direct-from-entities read path — see `wiki-curator-cog` ADR-002 for the renderer; the notes UI's data path is decided separately).
- Notes UI comes back online.
- After ~4 weeks of stability, reevaluate whether to drop `_legacy_*` tables. Default at reevaluation is to drop them; specific reasons to defer (active investigations, pending corrections against legacy data, identified migrations issues still being addressed) push the reevaluation forward by another 4 weeks. The decision is explicit each time, not automatic.

### Pre-flight checks

Before Phase 3 (the destructive step), three checks:

1. **Confirm every transcript in `NOTES_PROCESSED_FOLDER_ID` is intact and readable.** Listing + spot-check a sample of 5-10 for content sanity.
2. **Verify Drive copies match `wcs_transcripts.raw_text` for 3-5 random sources.** Protects against the worst case where archived files diverged from what was originally extracted.
3. **Validate the new prompt against one existing transcript by hand.** Pick a transcript the operator knows well; hand-list expected entities; run new prompt; compare. Single most important validation; iterate prompt if mismatch.

### Rollback

If Phase 3 reveals problems (entity resolver exploding, prompt regression not caught in Phase 0, corruption in Drive copies):
- `_legacy_wcs_notes` and `_legacy_wcs_transcripts` still exist; the operator can rename them back, point `transcription-cog` at the old write path (kept in git history), and serve from the legacy substrate while debugging.
- Cost of rollback: a few hours of operator work, no data lost.
- Rollback availability ends only when `_legacy_*` tables are dropped, which requires an explicit decision at the 4-week reevaluation point (Phase 3 completion + 4 weeks minimum).

## Consequences

### What this avoids

- **No parallel-run migration code.** No dual-write logic in `transcription-cog`. No compatibility view reconstructing `notes_json` from entities for arbitrary historical reads. No "two write paths coexist for now" period with its associated edge cases.
- **No two-tier corpus.** Every source is extracted under the same prompt version with the same entity structure. No "old extractions are thin, new ones are rich" debt.
- **No backfill-from-`notes_json` script.** Extracting entity structure from existing `notes_json` blobs would be lossy (the old extractions don't carry relationship info, drill purpose info, technique requirement info). The fresh extraction produces the right data from the right input.

### What this costs

- **LLM cost for re-extraction.** ~100 sources × $0.05-0.20 per extraction = $5-20. Bounded, modest.
- **Operator time for the rebuild.** A few hours of focused execution + validation. Not zero, but bounded.
- **Notes UI downtime.** Bounded to the rebuild window. Acceptable at proof-of-concept stage; communicated via a "Rebuilding" banner.
- **Loss of existing extractions as artifacts.** The current `notes_json` for each source disappears (preserved in `_legacy_*` during the rollback window, then gone). The operator has explicitly stated this is acceptable.
- **Risk of regressions in the new pipeline that the old pipeline didn't have.** Mitigated by Phase 0 (prompt iteration against test transcripts) and Phase 3's rollback availability.

### What this enables

- **Clean baseline for the new substrate.** The first canonical state under the new schema is produced by a single coherent pipeline. Easier to debug, easier to explain, easier to evolve.
- **Uniform extractor_version across the corpus.** Every source in the rebuilt state is at extractor v1.0.0 (or whatever version label is chosen for the rebuild). Future prompt iterations produce new `source_extractions` rows, comparable to the v1.0.0 baseline.
- **Wiki curator starts from canonical data.** No legacy markdown to preserve, no `phase-1-backfill` branch state to migrate. The first rendered wiki under the new substrate replaces whatever was there.

### What this rules out

- **Treating any specific existing extraction as authoritative.** Once the rebuild runs, the new extraction is the canonical one for each source. The operator has accepted this.
- **Mid-flight uptime guarantees.** The notes UI will be offline during the backfill window. No HA, no zero-downtime migration.

## Alternatives considered

**Parallel-run migration with eventual deprecation.** Rejected. The transition code is substantial (compatibility views, dual-write, careful staging), all of it transitional and thrown away after deprecation. The proof-of-concept stage makes this overhead unjustified.

**Backfill from existing `notes_json` into the new substrate without re-extracting.** Rejected. The new schema asks for entity structure (kinds, relationships, drill purposes, technique requirements) that's not in current `notes_json`. A mechanical backfill would produce structurally thin entity rows requiring re-extraction anyway. Two operations where one suffices.

**Re-extract incrementally over time, no rebuild.** Rejected for the proof-of-concept stage. Incremental re-extraction is what ADR-0003's versioned-extractions design enables for *future* prompt iterations. For the initial substrate migration, incremental re-extraction means the corpus is two-tier until completion — a state that lasts months and complicates every query in the meantime. Clean rebuild is faster end-to-end.

**Hold the current substrate; defer the substrate change indefinitely.** Rejected. The substrate change is the foundation for every future capability (rich Q&A, assessment, public sharing, corrections-as-data). Deferring it is deferring the project's value.
