# ADR-0003 — Versioned extractions and corrections

**Date:** 2026-05-19
**Status:** Accepted
**Related:** ADR-0002 (entity substrate)

## Context

ADR-0002 establishes the entity substrate: canonical WCS knowledge lives as normalized entities in Postgres, derived from inputs by a Composition Service. This ADR specifies the *input layer* that Composition reads from.

Two operational realities motivate the input layer's design:

**Extraction quality evolves.** The LLM prompt for transcript-to-claims extraction will be iterated over time. Better prompts produce better extractions. The system needs to accommodate "the extraction we have now" vs. "the extraction we'd produce today if we re-ran" without conflating them, and without forcing full-corpus re-extraction every time a prompt improves.

**Extractions can be wrong, and correcting them shouldn't require re-extracting.** The LLM mishears names, mis-classifies concepts as techniques, drops claims that were in the transcript, or extrapolates claims that weren't. Some of these errors are correctable by an operator who knows the lesson better than the LLM does. The correction should propagate downstream (to the wiki, the Q&A agent, future assessment) without requiring a re-extraction event.

**Some content is missing from transcripts entirely.** Insights about a lesson that emerge later, context that wasn't said aloud during recording, cross-source observations — these don't belong in the extraction at all. They're additions to the corpus, made by the operator, that should sit at the same authority level as extracted content once added.

The previous design (`wcs_notes.notes_json` overwritten on re-extraction, no correction layer, no addition layer) handles none of these well. Re-extraction destroys the old output. Corrections require either editing JSON in the database or re-extracting with a corrected prompt — both heavy operations. Additions have no schema home at all (today's `notes_json` is "what the LLM produced for this transcript"; manually-authored content has nowhere to go without being mistaken for LLM output).

## Decision

The input layer comprises **versioned extractions** (immutable LLM outputs, one active at a time) plus **typed correction and addition records** that compose with extractions to produce the canonical entity layer.

### Versioned extractions

```
source_extractions
  id, source_id,
  extractor_version,                 -- semver of the extraction pipeline at extraction time
  extractor_model, extractor_provider,
  prompt_version,                    -- separate from extractor_version; prompt may evolve independently
  raw_output jsonb,                  -- the LLM's actual structured output
  extracted_at,
  is_active boolean,
  notes                              -- optional: why this extraction was promoted/demoted

  UNIQUE (source_id) WHERE is_active = true
```

Properties:

- **Multiple extractions per source coexist.** Re-extracting a source `INSERT`s a new row; the previous extraction persists.
- **Exactly one extraction is active per source.** The unique constraint enforces this. `is_active = true` marks the extraction that the Composition Service reads from.
- **Promotion is one operation.** Switching which extraction is active is an `UPDATE is_active` transaction. The Composition Service re-runs for that source; the canonical layer updates.
- **Old extractions are queryable.** "Show me how this source was extracted under prompt v1 vs. prompt v2" is a SQL diff between two `raw_output` blobs. Useful for A/B prompt evaluation, regression hunting, and trust-building when iterating extraction quality.
- **Rollback is one operation.** A bad re-extraction is reversed by re-promoting the previous active extraction. No data lost.
- **Re-extraction is opt-in per source.** Backfilling the entire corpus under a new prompt is *possible* but never *required*. The operator chooses which sources to re-extract based on cost, value, and confidence in the new prompt.

This shape replaces the single mutable `wcs_notes.notes_json` column with a history of extraction events. Storage cost is modest (a few jsonb blobs per source, no real growth over time unless prompts iterate aggressively). Operational cost is paid in clarity.

### Correction records

Corrections override individual fields produced by the active extraction. They are typed, scoped, and source-attributed.

```
name_corrections
  id, raw_name, corrected_name,
  scope,                             -- 'global' | source_id-specific
  reason, created_at, created_by
  -- "Every time the LLM transcribed 'Roberta Royston', read it as 'Robert Royston'"

attribution_corrections
  id, source_id,
  attribution_target,                -- identifies which extracted attribution to correct
  field,                             -- 'entity' | 'instructor' | 'prose' | 'kind' | ...
  corrected_value,
  reason, created_at, created_by

source_metadata_corrections
  id, source_id, field, corrected_value, reason, created_at, created_by
  -- "Filename said 2025-09-15 but the actual lesson was 2025-09-22"
```

Properties:

- **Corrections override extraction without modifying it.** The extraction's `raw_output` is preserved; corrections compose at Composition Service time.
- **Corrections are scoped where appropriate.** Name corrections can be global (always interpret X as Y) or source-specific (just for this lesson). Attribution corrections are per-source by construction.
- **Corrections are themselves auditable.** Every correction has a `reason` and a creator. The history of how the canonical state diverged from the raw extraction is preserved.
- **Re-extraction doesn't invalidate corrections.** If a new extraction is promoted, attribution corrections referencing specific attributions may stop matching (the new extraction may have different attribution structure). The Composition Service must handle gracefully — a correction that doesn't match anything in the active extraction is logged but doesn't break anything.

### Addition records

Additions insert content into the canonical layer that doesn't come from any extraction. They are first-class additions, not corrections to absent content.

```
attribution_additions
  id, source_id,
  entity_slug,                       -- which entity this attribution is about
  instructor_slug NULL,              -- who taught/asserted this; NULL if no specific instructor
  attribution_kind,
  prose,
  reason,
  created_at, created_by
  -- "Kate also mentioned X about settle that the LLM missed"
  -- "I realized later this lesson connects to Y"

drill_purpose_additions
  id, drill_entity_slug,
  source_id NULL,                    -- NULL if not tied to a specific source
  skill_name, prose, focus_context,
  reason, created_at, created_by

technique_requirement_additions
  id, technique_entity_slug,
  source_id NULL,
  skill_name, prose,
  reason, created_at, created_by

entity_relation_additions
  id, from_entity_slug, to_entity_slug,
  relation_kind, prose,
  reason, created_at, created_by
```

Properties:

- **Additions are equal-status with extractions in the canonical layer.** The Composition Service merges extracted content and added content into the same `source_attributions` (or `drill_purposes`, `technique_requirements`, `entity_relations`) tables. Only the `origin` field on the canonical row distinguishes them.
- **Additions can fill gaps the LLM doesn't surface.** An operator browsing the wiki notices a technique with no requirements documented. They `POST` a `technique_requirement_addition`. The Composition Service incorporates it; the wiki next render reflects it.
- **Additions can tie to a source or be global.** A source_id-tied addition appears as a contribution to that source's claims. A NULL source_id addition is unattributed to any lesson (manual operator knowledge).

### The Composition Service's job

The Composition Service is the deterministic function:

```
Composition(source) =
  active_source_extraction(source)
    ⊕ all corrections matching this source
    ⊕ all additions matching this source
    ⊕ relevant global corrections (e.g. name_corrections)
  →  the canonical Layer 2 rows for this source's contribution
```

It runs:
- When a new source is ingested (new `sources` row + first `source_extraction`)
- When a correction or addition is added
- When an extraction is promoted to active (new `is_active = true`)
- On demand (full re-compose for a source, or for the whole corpus)

Re-composing a single source is cheap (one row in `sources`, one active extraction, a handful of correction/addition rows). Re-composing the whole corpus is bounded (no LLM calls; pure SQL/Python work) and used for substrate-design changes that change how composition itself works.

### Origin tracking

Every row in the canonical layer carries an `origin` field:

- `extraction` — produced from an active source_extraction
- `manual` — produced from a correction or addition
- `inferred` — produced by deterministic logic in the Composition Service (e.g., depluralization → alias)
- `merge` — produced by an entity merge operation

This preserves auditability without privileging one origin in queries. A wiki page rendering attributions doesn't differentiate by origin in the markdown (they're all equally part of "what the system knows"), but a curation UI can highlight manual content distinctly.

## Consequences

### What this enables

- **Cheap re-extraction.** Re-extracting a single source under a new prompt costs one LLM call. The previous extraction persists; the operator compares output side-by-side before promoting.
- **A/B prompt evaluation.** Run a new prompt against 5 sources, compare `raw_output` blobs across versions, decide whether the new prompt is better. No commitment, no cleanup.
- **Reversible re-extractions.** If a new extraction is worse, demote it. The previous extraction is still there. Composition re-runs.
- **Corrections without re-extraction.** Mishearings, mis-classifications, and other extraction errors are fixable by a single API call. No LLM cost.
- **Additions as gap-filling.** Operator-written content has a proper home. The wiki surfaces gaps; the operator fills them; the system reflects the addition immediately.
- **Audit trail.** Every change to the canonical layer is traceable: which extraction produced this attribution, which correction overrode it, which addition supplemented it.
- **Bounded re-derivation cost.** Re-running the Composition Service is cheap because it's not LLM work. Schema changes that affect composition logic can re-derive the canonical layer overnight.

### What's harder

- **The Composition Service must handle inconsistency gracefully.** A correction that referenced an attribution in extraction v1 may not match anything in extraction v2. The service must log this and continue; the operator can re-issue the correction against the new extraction if it still applies.
- **Origin tracking is everywhere.** Every canonical row carries `origin`. The Composition Service must set it correctly across many code paths. Easy to drop on accident.
- **Storage grows with extraction count.** Each re-extraction adds a row to `source_extractions` with its `raw_output` blob. For a corpus of ~100 sources extracted under 3-4 prompt versions, this is a few hundred blobs — fine. At meaningful corpus scale (thousands of sources, many prompt iterations), pruning old extractions may become worthwhile.
- **The Composition Service is a SPOF for correctness.** If it has a bug, the canonical layer is wrong even though the inputs are correct. Mitigated by: idempotency (re-running fixes drift), comprehensive testing against synthetic inputs, and the ability to re-derive from scratch.

### What this rules out

- **In-place mutation of extracted content.** No endpoint lets an operator edit a `source_extractions.raw_output` blob. Mutation is via correction or new extraction. This is intentional — preserving the LLM's actual output is essential for debugging and prompt evaluation.
- **Hand-editing the canonical layer.** Operators don't `UPDATE source_attributions SET prose = ...`. Corrections and additions go through the input layer; the Composition Service produces canonical state. This keeps the canonical layer a pure function of inputs, which makes re-derivation always correct.

## Alternatives considered

**Single mutable extraction per source.** The current design. Rejected — overwriting on re-extraction loses the prior output, makes prompt iteration risky, and conflates "what the LLM said" with "what we know now."

**Append-only extractions without an `is_active` flag.** Rejected — without an active marker, the Composition Service has no way to know which extraction to read from. Could be solved with "latest by extracted_at" semantics, but that's less flexible (no rollback, no A/B without manual reordering).

**Corrections as edits to the active extraction's raw_output.** Rejected — destroys the LLM's actual output. Indistinguishable from a re-extraction with different output. Loses the property that the LLM's output is the LLM's output.

**Corrections via prompt augmentation** ("re-extract this source with these hints"). Rejected — couples corrections to re-extraction cost, doesn't help when the correction is something the LLM can't see (e.g., context from outside the transcript).

**No correction layer; rely entirely on re-extraction with better prompts.** Rejected — re-extraction is expensive (LLM calls, prompt iteration time). Many corrections are pointwise fixes that don't require prompt-level changes (a specific name mishearing, a specific mis-classification). Forcing them through re-extraction is overkill.

**Hand-curated YAML files for corrections** (per-source `.corrections.yaml` files in a git repo). Rejected — duplicates the existing `_aliases.yaml` machinery in `wcs-wiki`. The substrate is the API's Postgres; corrections should live there too, not in a parallel file-based system.
