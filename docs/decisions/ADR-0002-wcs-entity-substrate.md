# ADR-0002 — WCS entity substrate

**Date:** 2026-05-19
**Status:** Accepted
**Supersedes:** Implicit "JSON blob in `wcs_notes.notes_json`" data model
**Related:** ADR-0003 (versioned extractions + corrections), ADR-0004 (rebuild from scratch)

## Context

The WCS pipeline today stores structured notes as JSON blobs (`wcs_notes.notes_json`) produced by an LLM extraction over lesson transcripts. Multiple consumers — the notes UI (`wcs.kaianolevine.com/notes`), the wiki (`wcs-wiki` repo, built by `wiki-curator-cog`), and the Q&A agent (`POST /v1/wcs/ask`) — each rebuild structure from this flat representation in their own way.

This produced friction visible across the ecosystem:

- `wiki-curator-cog` is ~3,000 lines that reconstruct a graph (concepts ↔ teachers ↔ sources, vocabulary equivalences, attribution prose) from flat JSON, with ~200 lines of quality filters compensating for upstream extraction noise.
- Cross-cutting queries ("every concept Kate has taught", "every source where Kaiano taught Kate") are implemented by scanning every note row and parsing its `notes_json`, either at curator time (markdown views) or at render time (wiki views).
- Identity decisions (is "anchor", "anchor-step", "anchored step" the same concept?) are answered three times: in the extraction prompt, in the curator's alias-map machinery, and at render-time synthesis.
- Schema evolution requires re-extraction. Adding a new structural field to `notes_json` means changing the prompt and re-running the LLM over every transcript.
- The Q&A agent retrieves at the note grain (notes that mention X) rather than the entity grain (concept X, with its attributions), which produces lesson-level answers when concept-level answers would be more useful.
- Provenance loses fidelity at every layer. A specific claim ("Kate's framing of settle in the 2025-09-15 lesson, paragraph 4") survives only as a wikilink in markdown; the canonical store doesn't carry attribution at all.

The wiki was described in its own `CLAUDE.md` as "derived, not authoritative" — but the implementation treated it as a system of record, doing graph-construction work over markdown. The data is naturally a graph of attributed claims; storing it as JSON blobs and reconstructing structure downstream is the wrong shape.

This ADR commits to the alternative: a knowledge graph in the API's Postgres as canonical, with the notes UI, the wiki, and the Q&A agent as views over it.

## Decision

**The canonical representation of WCS knowledge is a normalized entity graph in `api-kaianolevine-com`'s Postgres.** Every other surface (notes UI, wiki markdown, Q&A agent retrieval) is a view over this substrate.

### Three-layer model

The substrate organizes into three layers with distinct properties:

**Layer 1 — Inputs (immutable, append-only).** Records of what was extracted or asserted. This layer is the only thing that takes writes from outside the API. Inputs compose to produce Layer 2.

**Layer 2 — Canonical entities (derived, but durable).** The graph that downstream consumers query. Derived from Layer 1 by a composition service, but with its own polish prose state (Layer 3 below) that persists across re-derivation.

**Layer 3 — Polish prose (LLM-written or manual, preserved across re-derivation).** Prose fields on entity rows (`overview_md`, `background_md`, etc.) that survive re-extractions because they live on canonical entities, not on the inputs that produce them.

Layer 1 is the substrate's write surface. Layer 2 is its read surface. Layer 3 is the editorial layer that gives the wiki something to render beyond mechanical attribution lists.

### Entity kinds

Four entity kinds are first-class in Layer 2. They share the same `entities` table differentiated by a `kind` column:

- **concept** — an abstract idea, often compound. "Musicality" is composed of phrasing, accent recognition, use of breaks. "Connection" is composed of frame, weight commitment, compression and leverage. Concepts can contain other concepts.

- **technique** — a tool to execute a skill. "Anchor step", "bow-and-snap", "lead the slot, not the partner." Techniques are means; they're executed in service of a dance outcome.

- **pattern** — a named figure in WCS movement vocabulary. "Sugar push", "whip", "basket whip", "left-side pass." Patterns may have multiple names across people or regions; this is handled via aliases, not by tracking dialect.

- **drill** — an exercise to practice a technique. "The paper drill", "10-second walk", "Robert's connection drill." Drill names are often arbitrary handles; the drill's purpose (what skill it develops) is what carries the meaning.

The kind boundaries are fuzzy in real data (anchor step is plausibly both a concept and a technique). The model accepts this via the correction layer: when an entity is wrongly kinded, it is corrected. Pedantic-but-useful kind labels beat both "everything is an `entity` row" (which loses semantic distinction) and "one table per kind" (which makes cross-kind queries awkward).

### Vocabulary, definitions, and aliases

Vocabulary is not a fifth kind. It is the *naming surface* over all four kinds.

- **`entity_aliases`** captures that "anchor step", "anchor-step", "the anchor", "anchored step" all refer to the same entity. Aliases originate from extraction variants, depluralization, and manual merges.
- **`entity_definitions`** captures per-source vocabulary definitions: "Kate defined settle as weight commitment in the lesson on 2025-09-15." Distinct from attribution prose because a definition is a "this is what this word means" assertion, not a teaching framing.

A vocabulary term in a lesson maps to: an `entities` row (creating it if new), an `entity_aliases` row if the term is a variant of an existing alias, and an `entity_definitions` row if the source explicitly defined the term.

### The skill layer (Layer 1.5)

Skills exist as a real category but are *not* a fifth entity kind. They emerge from the corpus rather than being independently catalogued.

The structure is asymmetric:

- **`drill_purposes`** captures what skill a drill develops. A drill's name is often arbitrary ("the paper drill"); its purpose carries the meaning. The same drill can have multiple purposes — different focuses, different things the instructor wants attended to during the same procedure.

- **`technique_requirements`** captures what skills a technique requires to execute. Many techniques have requirements that no source has yet articulated; this is a real gap that the model preserves rather than hides.

Both tables share a `skill_slug` namespace. When a drill's purpose ("smooth weight transfer at controlled tempo") matches a technique's requirement, the shared slug links them — a drill that develops X is paired with the techniques that require X. This is the corpus's emergent path from "I want to do this technique" to "drill this skill."

No `skills` table exists. The `skill_slug` is the dedup handle; the corpus is its definition. No alias machinery for skills yet (accept duplicate slugs until reconciliation matters; reconcile manually when querying surfaces duplicates).

This asymmetry — drills point at skills they develop, techniques point at skills they require, skills themselves aren't entities — captures something real about the discipline: skills are the *bridge* between training (drills) and execution (techniques), and a skill is most clearly defined by what it operationalizes (a drill) and what it serves (a technique), not by an independent description.

### Relations between entities

Cross-kind structure is captured in `entity_relations`. Examples of relation kinds that emerge from the corpus:

- `drill_trains_technique` (a drill teaches the use of a technique)
- `pattern_executes_via_technique` (a pattern is performed by applying techniques)
- `pattern_variant_of` (one pattern is a named variant of another)
- `concept_informs_technique` (a concept shapes how a technique is applied)
- `concept_contains_concept` (a compound concept is made of sub-concepts)
- `technique_serves_pattern` (a technique exists primarily within a pattern)

`relation_kind` is a free string for now. After enough corpus has accumulated, common relation kinds will be obvious and can be promoted to enumerated values or typed tables. Today, the corpus doesn't know enough to prematurely structure them.

Relations carry an optional `source_id` (which lesson asserted this relationship) and `prose` (how the source described it), so relations themselves can be source-attributed.

### Composition service

The Composition Service is the deterministic function from Layer 1 (inputs) to Layer 2 (canonical entities). Given the active `source_extraction` for a source, plus any applicable corrections and additions, it produces:

- The right `entities` rows (creating new ones for new slugs, looking up existing via alias)
- `source_attributions` rows attaching the source's claims to entities, attributed to the source's instructors
- `entity_definitions` rows for vocabulary terms the source defined
- `entity_relations` rows for relationships the source asserted
- `drill_purposes` rows for drill purposes the source named
- `technique_requirements` rows for technique requirements the source named
- `source_references` rows for people the source mentioned but didn't teach

The Composition Service is the spiritual heir of `wiki-curator-cog`'s `plan_contributions` — same routing logic, in the right place. It runs at the API level, has no LLM dependency (the upstream extraction is the LLM step), and is deterministic: same inputs always produce the same Layer 2 output.

Triggered by: ingest of a new source, addition of a new correction, addition of a new attribution_addition, promotion of a new extraction to active.

### Gaps as first-class

The model is *honest about gaps* by design. An empty `technique_requirements` for a technique means "no source has described what skills this requires"; an empty `drill_purposes` for a drill means "no source has described what this trains." A `skill_slug` appearing in one table but never the other means the corpus has incomplete knowledge of how it's developed or used.

Gap-surfacing queries are first-class:

- "Which techniques have no extracted requirements?"
- "Which drills have no extracted purposes?"
- "Which skill_slugs appear in drill_purposes but no technique_requirements?"

These are SQL queries, exposed via admin endpoints. The wiki view can surface gaps to operators visually (a technique page with no `requires` section signals the gap).

Filling a gap is a small operation: a single API call inserts a row in `technique_requirements` or `drill_purposes`, the Composition Service incorporates it, the wiki regenerates on next export. Gap-filling is *equal in status* to LLM-extracted content in the canonical layer — only the `origin` field distinguishes them.

This means the system is, in part, a tool for noticing what the user doesn't know. The wiki prompts articulation of tacit knowledge by surfacing under-described entities. The user filling gaps is a primary intended workflow, not an exception.

## Schema sketch

The full schema lives in migration 019 (separate file). The sketch:

```
sources                              -- the lesson (≈ today's wcs_notes minus notes_json semantics)
  id, owner_id, transcript_id,
  title, session_date, session_type, organization,
  instructors_raw text[], students_raw text[],  -- verbatim from filename, authoritative
  is_default_visible, visibility,
  created_at

source_extractions                   -- versioned LLM outputs (see ADR-0003)
  id, source_id,
  extractor_version, extractor_model, extractor_provider, prompt_version,
  raw_output jsonb,
  extracted_at,
  is_active boolean

entities                             -- canonical WCS-domain things
  id, slug UNIQUE, canonical_name,
  kind,                              -- 'concept' | 'technique' | 'pattern' | 'drill'
  overview_md,                       -- polish (Layer 3); preserved across re-extraction
  status,                            -- 'stub' | 'draft' | 'mature' (derived from attribution count)
  merged_into_id NULL,               -- soft-delete via merge; never hard-delete
  created_at, updated_at

entity_aliases                       -- the naming/vocabulary surface
  id, entity_id, alias UNIQUE,
  origin                             -- 'extraction' | 'manual' | 'depluralized' | 'merge'

entity_definitions                   -- per-source vocabulary definitions
  id, entity_id, source_id, instructor_id NULL,
  term, definition, position

entity_relations                     -- cross-entity edges; free-string kind for now
  id, from_entity_id, to_entity_id,
  relation_kind,                     -- free string
  source_id NULL,
  prose NULL,
  origin                             -- 'extraction' | 'manual' | 'inferred'

source_attributions                  -- the claim layer
  id, source_id, entity_id, instructor_id NULL,
  attribution_kind,                  -- 'taught' | 'mentioned' | 'demonstrated' | 'mistake'
  prose, raw_term, position,
  drill_goal, drill_steps text[],    -- nullable; only for drill attributions
  mistake_text, correction_text      -- nullable; only for mistake attributions

source_references                    -- people mentioned in a source (not the instructor)
  id, source_id, instructor_id, context, ref_type

instructors                          -- people; both teachers and students are people
  id, slug UNIQUE, canonical_name,
  background_md, teaching_themes_md, notable_framings_md,  -- polish (Layer 3)
  merged_into_id NULL

instructor_aliases
  id, instructor_id, alias UNIQUE, origin

drill_purposes                       -- skill layer: what skill this drill develops
  id, drill_entity_id, source_id NULL,
  skill_name, skill_slug,            -- shared namespace with technique_requirements
  prose, focus_context,
  origin

technique_requirements               -- skill layer: what skills this technique needs
  id, technique_entity_id, source_id NULL,
  skill_name, skill_slug,            -- shared namespace with drill_purposes
  prose,
  origin
```

The input layer (corrections, additions) is detailed in ADR-0003.

## Consequences

### What gets simpler

- `wiki-curator-cog` becomes ~500 lines instead of ~3,000. It's a renderer that reads canonical data and writes markdown. Filter logic moves upstream (validation at extraction time). Synthesis logic disappears (joins replace it). Alias-map mutation disappears (corrections are an API call).
- The Layer 1 / Layer 2 coordination problem (deferred in `wiki-curator-cog`'s ADR-001) dissolves. There is no mechanical L1 pass to coordinate with; polish prose lives on canonical rows and survives every re-derivation.
- Cross-cutting queries become SQL joins. "Every concept Kate has taught": `source_attributions JOIN sources WHERE instructor_id = kate`. Today: scan every note, parse every `notes_json`, reconstruct.
- The Q&A agent retrieves at entity grain. New retrieval tools (`search_concepts`, `search_techniques`, `search_drills`) operate over Layer 2 directly. The "third corpus to bolt on" problem dissolves; the wiki *is* the entity layer.
- Schema evolution doesn't require re-extraction. Adding a new field to `entities` or a new attribution kind is a migration, not an LLM run.

### What's harder

- The Composition Service is the single most important new piece of code. It must be deterministic, idempotent, and correct across the full range of `source_extractions` outputs. Bugs here corrupt the canonical layer silently.
- Entity resolution (slug + alias + depluralization + merge-aware) must be right at write time. A miscategorized entity persists until corrected; a miss-aliased entity creates a duplicate that the corpus accumulates around.
- Schema mistakes are more expensive than JSON-blob mistakes. Adding a column is cheap; restructuring the relationship between `entities` and `entity_relations` later means a real migration. Mitigated by keeping the proof-of-concept entity model minimal — split into more tables only when corpus motivates it.
- The notes UI either changes shape or gets a compatibility layer (denormalize Layer 2 back into a `notes_json`-shaped JSON for read). The transition is real but bounded (see ADR-0004).

### What's deferred explicitly

These are real future work, named here so they are findable:

- **Skills as first-class entities.** Today: skill_slug is a dedup handle in `drill_purposes` and `technique_requirements`, no `skills` table. When/if the assessment layer arrives, skills may be promoted to entities with hierarchy, overview prose, and assessment infrastructure. The data the model collects now (per-source drill purposes, technique requirements) is sufficient signal to design the eventual skill catalog when the corpus motivates it.
- **Skill alias machinery.** Today: duplicate skill_slugs are accepted; reconciliation is manual. When skills become primary query targets, a `skill_aliases` table or equivalent will be added.
- **Typed relation tables.** Today: `entity_relations.relation_kind` is a free string. Common relation kinds will emerge from corpus data and may be promoted to enumerated values or dedicated tables. Not pre-structured because the corpus doesn't yet know which structures it wants.
- **Dancer skill assessments.** A future per-person state layer reading from drill_purposes + technique_requirements + student_observations (preserved on `source_extractions.raw_output`) to produce `dancer_skill_state`. Not in the proof-of-concept scope; the substrate is designed to support it without further schema rework.
- **Curation UI.** Today: corrections and additions are API calls. A web UI for entity curation lives on `wcs.kaianolevine.com/admin` as future work.
- **Dialect / regional variation.** Today: a pattern with multiple names is one entity with multiple aliases. No tracking of which name belongs to which region or tradition. If this becomes a real need, an `entity_alias_dialects` table can be added.
- **Per-paragraph transcript binding.** Today: attribution prose is plain text. A future enhancement could bind each attribution to a specific transcript span for deep provenance. Not in scope.

### What this enables that the previous design couldn't

- **Longitudinal queries.** "How has Kaiano's framing of musicality evolved across his lessons?" — `source_attributions WHERE concept = musicality AND instructor = kaiano ORDER BY session_date`.
- **Vocabulary cross-cuts.** "Every term Robert uses for what Kate calls settle" — `entity_aliases` joined to `entity_definitions` filtered by instructor.
- **Concept decomposition.** "What sub-concepts does musicality contain?" — `entity_relations WHERE from = musicality AND kind = concept_contains_concept`.
- **Gap-finding queries.** "Which techniques have no extracted requirements?" — `entities LEFT JOIN technique_requirements WHERE technique_requirements.id IS NULL AND kind = technique`.
- **Merge as a transaction.** "Anchor and anchor-step are the same; merge them" — `UPDATE entities SET merged_into_id = anchor_step.id WHERE id = anchor.id; UPDATE source_attributions SET entity_id = anchor_step.id WHERE entity_id = anchor.id; INSERT INTO entity_aliases ...`. Reversible, auditable, atomic.
- **Q&A at entity grain.** The agent retrieves "the concept settle, taught by these 4 sources, with these attribution paragraphs" instead of "5 notes mentioning settle, figure it out."
- **Future assessment.** When skills are eventually promoted to first-class entities, the data to design the catalog is already collected in `drill_purposes` and `technique_requirements`. Six months of corpus tells you which skill_slugs matter.

## Alternatives considered

**Keep `notes_json` blob; add a graph layer on top.** Rejected. The graph would be a derived materialization — same problem we have today with `wiki-curator-cog`, just relocated into the API. The blob's flat shape is exactly what we're trying to move past.

**Use a graph database (Neo4j, etc.) instead of Postgres.** Rejected for proof-of-concept. The relations are real but the scale is small (hundreds of entities, thousands of attributions). Postgres handles this comfortably with normal foreign keys and indexes, and avoids introducing a new data-store dependency. If query patterns ever push past what Postgres handles well, graph-DB migration is a future decision with much more signal to inform it.

**Skills as a fifth entity kind from day one.** Rejected. Skills don't have the same shape as concepts/techniques/patterns/drills — they're compositional, gradient, and the meaningful trainable ones are operationally identical to their drills. Making them a kind would force premature structuring; deferring lets the corpus tell us what skill structure to build. (See "The skill layer (Layer 1.5)" above.)

**Typed relation tables from day one** (`drill_trains_technique`, `pattern_variant_of`, etc., each its own table). Rejected. We don't yet know which relation kinds will be common or how their shapes will diverge. Free-string `relation_kind` on `entity_relations` is loose enough to capture whatever the LLM produces, and tight enough that we can survey what's emerged after a few months of corpus data. Premature normalization is a real risk here.

**Parallel-run migration** (keep `wcs_notes`, build entities alongside, migrate consumers gradually). Rejected in favor of clean rebuild — see ADR-0004 for that decision.
