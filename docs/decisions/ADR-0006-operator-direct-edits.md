# ADR-0006 — Operator direct edits on canonical state

**Date:** 2026-05-28
**Status:** Accepted
**Supersedes (partially):** ADR-0003 (refines the immutability boundary)
**Related:** ADR-0002 (entity substrate), ADR-0003 (versioned extractions and corrections), ADR-0004 (rebuild from scratch)

## Context

ADR-0003 established a write model for the WCS substrate centered on **input-layer modifications composed at compose-time**. The principle: extractions are immutable LLM outputs; operator changes happen by adding correction records (`name_corrections`, `attribution_corrections`, `source_metadata_corrections`) or addition records (`*_additions`) that overlay extractions when the Composition Service runs. Canonical-layer rows are *computed*, not directly edited.

The ADR's phrasing ("In-place mutation of extracted content. No endpoint lets an operator edit a `source_extractions.raw_output` blob. Mutation is via correction or new extraction.") was scoped to extraction blobs, but in practice it was read as ruling out direct mutation of canonical rows generally.

Practice has diverged from that reading in two ways:

**1. Direct-edit endpoints on canonical rows already ship.** `PATCH /v1/wcs/admin/sources/{id}` and `PATCH /v1/wcs/admin/sources/{id}/visibility` directly mutate `wcs_sources` columns. They do not go through correction records. They were added when the immediate need arose, without an ADR amendment.

**2. Data-quality problems exist that the correction layer doesn't fix ergonomically.** Examples surfaced after the corpus reached 87 sources:

- Two canonical instructor rows are the same person, spelled differently across source filenames (`Robertroyston` vs `Robert-Royston`). A `name_correction` configures future resolution but doesn't fix the existing duplicate canonical row, which has attributions pointing at it that need to move.
- An instructor's canonical name was inferred from a filename typo (`KristenWallace`) and the operator wants to rename the canonical row to `Kristen Wallace`. The `name_correction` mechanism would change future resolution but leave the existing row's `canonical_name` text as it was.
- Five synthetic instructor rows (`kaiano-amy`, `kaiano-danika`, …) were created by composition when filename labelling errors put two names into one position. After `source_metadata_corrections` fix the filenames and recompose moves the attributions, the synthetic rows are orphan dead rows that should simply be deleted.

The corrections-only model handles these indirectly at best. Direct edit is the natural tool.

**3. Re-extraction is rare-to-never in practice.** ADR-0003 explicitly designed extractions as opt-in re-runnable, and Section 7 of the pipeline documentation re-affirms that this capability exists but is reserved for prompt evolution applied to new transcripts. Retroactive re-extraction of the existing corpus is theoretical; it has not happened and is not planned. This matters because one of the implicit objections to direct edits on canonical rows — "re-extraction will overwrite them" — is not a real operational concern.

This ADR formalizes the position: **canonical-layer rows are directly editable by operators. The immutability boundary applies to input-layer rows (transcripts and extraction outputs) only.**

## Decision

### The immutability boundary

**Input-layer rows are immutable:**

- `wcs_transcripts.text` — the historical record of what was said.
- `wcs_source_extractions.raw_output` — the historical record of what the LLM produced for a specific `(transcript, prompt, model)` tuple.

No operator endpoint exists for either. They are preserved indefinitely for prompt evaluation, debugging, and auditing.

**Canonical-layer rows are operator-editable:**

- `wcs_sources` — already editable via `PATCH /v1/wcs/admin/sources/{id}` and `/visibility`. Formalized by this ADR; no behavioral change.
- `wcs_instructors`, `wcs_instructor_aliases` — newly editable per this ADR.
- `wcs_entities`, `wcs_entity_aliases` — newly editable per this ADR.
- `wcs_source_attributions`, `wcs_entity_definitions`, `wcs_entity_relations`, `wcs_drill_purposes`, `wcs_technique_requirements`, `wcs_source_references` — newly editable per this ADR.

### Operations

Direct edit on a canonical row supports these operations:

- **PATCH** — set specific fields. Slug uniqueness enforced where applicable.
- **DELETE** — hard delete. Refused with 409 if any live references exist; the operator repoints first.
- **Repoint** (only meaningful for canonical *identity* rows: instructors, entities) — move all FK references from this row to another row of the same type. Used to consolidate duplicates before deletion.
- **Alias add/remove** (only meaningful for canonical identity rows) — operate on the alias tables, not on the identity row itself.

### Relationship to the ADR-0003 correction layer

Direct edits do not replace corrections and additions. They have different jobs:

- **Direct edit** — "this canonical row is wrong; fix it." Affects the current canonical state immediately.
- **Correction record** (e.g. `name_correction`) — "this resolution rule should apply to current and future composition." Affects how the Composition Service interprets inputs.
- **Addition record** — "this content doesn't exist anywhere yet; create it." Inserts a new canonical row of operator authorship.

All three coexist as first-class write surfaces. Many cleanup workflows compose them: direct-edit fixes the existing duplicate row, then a global `name_correction` prevents the variant spelling from re-creating the duplicate on future ingest.

### Origin field semantics

`origin` records **creation provenance**, not modification history. An attribution that was originally extracted carries `origin: extraction` even after operator edits. The fact that it was edited is recorded in the audit log, not by mutating the origin.

This means a wiki-rendered or website-rendered view of canonical content cannot distinguish "this row was edited by the operator" from "this row was created by the operator as an addition." That distinction is available only via the audit log. The decision is deliberate: at render time, all canonical rows are equally authoritative; only retrospective debugging needs to know which were edited and when.

### Audit log

Every direct edit writes a row to a new `wcs_admin_audit_log` table:

```
wcs_admin_audit_log
  id              uuid primary key
  operator_id     text not null            -- Clerk sub of the editing user
  table_name      text not null            -- e.g. "wcs_instructors"
  row_id          uuid not null            -- the edited row's id
  operation       text not null            -- "patch" | "delete" | "repoint" | "alias_add" | "alias_remove"
  field_name      text                     -- e.g. "canonical_name"; null for delete/repoint
  before_value    jsonb
  after_value     jsonb
  reason          text                     -- optional, supplied by the operator
  created_at      timestamptz not null default now()
```

The log is **append-only**; rows are never modified or deleted. It is queryable via an admin endpoint (`GET /v1/wcs/admin/audit-log`) for debugging "what changed and when."

For PATCH operations, one row per changed field. For DELETE, one row capturing the row's full pre-delete state in `before_value`. For repoint, one row capturing the source and target ids. For alias add/remove, one row per alias.

### Re-extraction and direct edits

Re-extraction is rare-to-never (per the WCS pipeline document, Section "Re-extraction policy"). When it does happen, the Composition Service re-derives the affected source's per-source content rows. Direct edits to those rows for the affected source are overwritten by the recomputed canonical state.

This is a known property of re-extraction. Operators opting in accept that direct edits to the source's per-source content may be discarded. Re-extraction is reserved for cases where this trade-off is acceptable (typically: the prompt has materially improved, and the new extraction is expected to be better than the prior canonical state including its edits).

Re-extraction does **not** disturb canonical identity rows (instructors, entities) or their aliases. Composition's `resolve_instructor` / `resolve_entity` find the existing rows and re-use them. Direct edits to identity rows persist across re-extraction.

### Scope of this ADR's implementation

This ADR commits to the *decision*. The implementation lands in two passes:

**Pass 1 (next implementation cycle):**

- New endpoints: PATCH, DELETE, repoint, alias add/remove for `wcs_instructors` and `wcs_entities`.
- New migration: `wcs_admin_audit_log` table.
- Audit-log integration on every direct-edit endpoint, including the existing source PATCH endpoints (retroactive integration).

**Pass 2 (follow-up, when needed):**

- Direct-edit endpoints for per-source content rows (`wcs_source_attributions`, `wcs_entity_definitions`, etc.).
- The admin portal UI surfacing all direct-edit operations alongside the existing input-layer corrections and additions.

Pass 1 unblocks the immediate operator cleanup of the canonical-instructor mess accumulated through 87 sources. Pass 2 generalizes the pattern.

## Consequences

### What this enables

- **Direct cleanup of canonical state.** Operators can fix data without crafting correction records or running recomposes for every adjustment. Renames, deduplications, deletions of orphan rows all become single API calls.
- **The admin portal can be built.** ADR-0002 named the operator UI as deferred future work. Direct edits give the portal a coherent CRUD surface for canonical state, complemented by the existing correction/addition forms for input-layer changes.
- **Audit-log infrastructure for the substrate.** Once the audit log exists, future direct-edit endpoints (per-source content rows in Pass 2) compose into it for free.

### What this rules out, still

- **Editing transcripts.** Out of scope; `wcs_transcripts.text` remains immutable. If the need ever arises, it would be a meaningful design extension and a new ADR.
- **Editing extraction blobs.** Out of scope; `wcs_source_extractions.raw_output` remains immutable for prompt evaluation and historical reference.
- **Bulk operations across many rows in one call.** Each direct edit is one row. Bulk cleanup workflows compose individual calls (the portal makes this ergonomic).

### What this changes about the existing surface

- The existing `PATCH /v1/wcs/admin/sources/{id}` and `/visibility` endpoints are retroactively formalized as direct-edit operations. They gain audit-log integration in Pass 1; no behavioral change otherwise.
- ADR-0003's "in-place mutation is ruled out" is amended to apply specifically to `source_extractions.raw_output`. Canonical-layer in-place mutation is explicitly allowed by this ADR.

### What's harder

- **More operator surface to maintain.** Each canonical table now needs PATCH/DELETE endpoints and their tests. The portal that surfaces them is more code to write.
- **Two write models to choose between.** Operators must understand when to use direct edit vs. correction record. The WCS pipeline document includes a decision table for this.
- **Audit log volume.** Direct edits accumulate audit rows. Probably small for solo use, but worth being aware of as data volume grows.

## Alternatives considered

**Stay corrections-only and build merge-style endpoints for the cases corrections don't handle.** Considered. Would mean inventing a `wcs_instructor_merges` correction-like table to record "this row was merged into that one" and updating composition to follow the merge chain. Rejected because: (a) it would add another correction type for what is fundamentally a direct mutation; (b) the existing source PATCH endpoints already establish that direct edit is acceptable; (c) the operator's mental model for "I'm renaming this row" is more obviously a direct edit than a correction record. The corrections-only model was a clean abstraction in the design document but a poor fit for the data-quality realities of an operator-maintained corpus.

**Restrict direct edit to canonical identity rows only (instructors, entities, sources) and require corrections for per-source content rows.** Considered. Per-source content rows (attributions, definitions, etc.) are direct LLM output and ADR-0003's immutability instinct was strongest for them. Rejected because: (a) the `attribution_corrections` endpoint already lets operators override these — it's just heavier and not present for all content types; (b) re-extraction is rare-to-never, so the historical risk of "the direct edit gets overwritten" is not a practical concern; (c) the inconsistency ("you can edit this canonical row but not that one") is a worse model than uniform direct-edit authority.

**Treat all canonical state as immutable and require all operator changes to go through correction records or re-extraction.** Considered. This is the pure ADR-0003 model. Rejected because: (a) the source PATCH endpoints already violate it; (b) the data-quality issues that surfaced at 87 sources demonstrated that the indirect-via-correction path is too heavy for everyday cleanup; (c) re-extraction is not a practical fallback because it's reserved for prompt evolution.
