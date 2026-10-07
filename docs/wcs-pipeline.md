# WCS pipeline — end-to-end architecture

This document describes the West Coast Swing data pipeline from raw audio recording through the published wiki, naming each stage and the artifact it produces. It is the cross-cutting reference for how the substrate is built, written to, read from, and projected.

The pipeline spans five repositories:

- **`watcher-cog`** — polls Google Drive for new files and signals downstream.
- **`transcription-cog`** — the ingest pipeline: transcribes audio, runs LLM extraction, registers the source via the API.
- **`api-kaianolevine-com`** — owns the substrate (Postgres), runs the Composition Service, exposes read/write/admin endpoints.
- **`wiki-curator-cog`** — the stateless renderer that projects the canonical entity graph into markdown.
- **`website-astro-wcs`** — the SSR site that surfaces per-lesson notes and operator admin pages.
- **`wcs-wiki`** — the markdown projection of the canonical entity graph, browsable in Obsidian and on GitHub.

The ADRs that ground this pipeline:

- **`api-kaianolevine-com` ADR-0002** — the WCS entity substrate.
- **`api-kaianolevine-com` ADR-0003** — versioned extractions and corrections.
- **`api-kaianolevine-com` ADR-0004** — rebuild from scratch (the migration that produced the current substrate).
- **`api-kaianolevine-com` ADR-0006** — operator direct edits on canonical state.
- **`wiki-curator-cog` ADR-002** — repurpose curator as a renderer over canonical entities.
- **`wcs-wiki/CLAUDE.md`** — the render spec (v1.0).

---

## Pipeline overview

```
[lesson] → [audio file in Drive]
              │
              │ filename: YYYY-MM-DD instructors > students/org - Topic
              │ (filename-authoritative metadata)
              ▼
       watcher-cog (polls Drive)
              │
              │ signals new file
              ▼
       transcription-cog.process_transcript flow
       ┌────────────────────────────────────────────────┐
       │ 1. parse filename → ParsedFilename             │
       │ 2. read transcript text                        │
       │ 3. POST /v1/wcs/transcripts → wcs_transcripts  │
       │    (idempotent on drive_file_id)               │
       │ 4. LLM extraction call → extraction dict       │
       │ 5. POST /v1/wcs/sources                        │
       │    ├─ creates/updates wcs_sources              │
       │    ├─ writes wcs_source_extractions (active)   │
       │    └─ runs Composition Service synchronously   │
       │ 6. archive Drive file                          │
       └────────────────────────────────────────────────┘
              │
              ▼
       api-kaianolevine-com substrate
       (canonical-layer rows committed)
              │
              ├─────────────────┬──────────────────┬─────────────────┐
              ▼                 ▼                  ▼                 ▼
       wiki renderer    website notes UI    admin portal       Q&A agent
       (wiki-curator-   (website-astro-     (in website-       (future,
        cog)             wcs /notes/*)       astro-wcs           reads via
              │                                /admin/*)         retrieval
              ▼                                                   endpoints)
       wcs-wiki repo
       (markdown bundle)
```

The forward flow (input → canonical) runs through the cogs and the API. Operator edits and corrections write directly to the API; the canonical layer is regenerated when needed (recompose for corrections; direct edits take effect immediately).

---

## Stages, named

### 1. Recording

A lesson, class, or workshop is recorded. The output is an **audio file** in the configured Google Drive folder. The filename follows the convention:

```
YYYY-MM-DD instructors > students/org - Topic
```

Examples:

- `2026-05-15 Kaiano > Sarah - Anchor step quality`
- `2026-03-12 Robert Royston > Kaiano, Libby - Whip variations`
- `2025-09-08 Kaiano - Beginner WCS week 1`

The filename is **authoritative for who taught (`instructors`), who attended (`students`), the organization/context, the session date, and the topic**. The LLM downstream does not re-infer these fields from the transcript body; the filename is the source of truth.

A filename that doesn't parse cleanly is skipped by the cog with a `Sentry` warning and is not ingested.

### 2. Drive watch

`watcher-cog` polls the configured WCS notes folder on a schedule. When new files appear, it signals `transcription-cog` (via the existing inter-cog mechanism). `watcher-cog` itself is generic — it watches multiple folders for multiple cogs and is not WCS-specific.

### 3. Ingest pipeline

`transcription-cog` runs the `process_transcript` Prefect flow per detected file. The flow is single-instance (concurrency slot of 1) and processes files sequentially. Per file:

#### 3a. Parse filename

`filename_parser.parse_filename(name)` returns a `ParsedFilename` with `session_date`, `session_type`, `instructors` (list of raw name strings), `students` (list), `organization`, `title`. Parse failures skip the file.

The output is **filename-derived metadata** for use throughout the rest of the pipeline.

#### 3b. Read transcript

The audio file is read from Drive. (The transcription itself happens inside this step or as a sub-task; the result is plain transcript text.) Transcripts shorter than the configured minimum are skipped with a `transcript_too_short` reason.

The output is **transcript text** — a string of what was said, with whatever speaker-labelling and timestamp conventions the transcription model produces.

#### 3c. Transcript persistence

`POST /v1/wcs/transcripts` creates a row in `wcs_transcripts` containing the raw text, the `drive_file_id`, the parsed `source_type` inference, and an auto-assigned UUID. The endpoint is **idempotent on `drive_file_id`** (unique constraint `uq_wcs_transcripts_drive_file_id`); attempting to re-ingest a file with the same Drive id returns the existing row.

The output is **`transcript_id`**. This row is the substrate's permanent record of *what was said* and is **immutable** from the moment it lands. There is no operator surface for editing transcript text. (See ADR-0006 for the rationale.)

#### 3d. LLM extraction

The cog runs an LLM call against the transcript text plus the parsed filename metadata. The prompt asks the LLM to produce a structured representation: per-source attributions, definitions, relations, drill purposes, technique requirements, references. The output is a structured dict (a JSON blob).

The output is the **extraction**.

#### 3e. Source store + composition

`POST /v1/wcs/sources` (the cog's `task_store_source`). In one synchronous API call, the API:

1. Creates or updates the `wcs_sources` row with the filename-derived metadata.
2. Writes the extraction as a new row in `wcs_source_extractions`, marked active. Demotes any previously-active extraction for this source to inactive (but does not delete it — extractions are versioned and preserved).
3. Runs the **Composition Service** (`services/wcs_composition.py`) synchronously. The composition step derives all canonical-layer rows for this source:
   - Resolves each `instructors_raw` and `students_raw` name through `resolve_instructor`, producing `wcs_instructors` rows (creating new ones, finding existing by slug/alias, applying any matching `wcs_name_corrections`).
   - Resolves each entity reference in the extraction through `resolve_entity`, producing `wcs_entities` rows.
   - Writes the per-source content rows: `wcs_source_attributions`, `wcs_entity_definitions`, `wcs_entity_relations`, `wcs_drill_purposes`, `wcs_technique_requirements`, `wcs_source_references`.
4. Returns the refreshed `WcsSourceItem` only after all canonical rows are committed.

There is **no eventual consistency**. The canonical layer is current the instant the API call returns.

The output is the **canonical state** of the substrate for that source.

#### 3f. Archive

The processed Drive file is moved to the configured processed-files folder. This is the last step; if it fails, the source has already been ingested and is queryable, but the Drive file may need to be moved manually.

---

## The substrate (per ADR-0002 and ADR-0003)

After ingest, two persistent layers exist in Postgres.

### Input layer (immutable)

These rows are written by the ingest pipeline and are not subsequently edited.

| Table | Purpose |
|---|---|
| `wcs_transcripts` | Raw transcript text per Drive file. Idempotent on `drive_file_id`. |
| `wcs_source_extractions` | Versioned LLM outputs per source. Multiple may exist; one is active at a time. Preserved for prompt evaluation and historical reference. |

Plus the operator-authored **correction and addition records** (also input-layer; see "Corrections" below):

| Table | Endpoint | Purpose |
|---|---|---|
| `wcs_name_corrections` | `POST /v1/wcs/admin/corrections/name` | Override raw name resolution during `resolve_instructor` / `resolve_entity`. Scope: global or per-source. |
| `wcs_attribution_corrections` | `POST /v1/wcs/admin/corrections/attribution` | Override fields on individual attributions in a specific source. |
| `wcs_source_metadata_corrections` | `POST /v1/wcs/admin/corrections/metadata` | Override filename-derived source fields (`field`: `instructors`, `students`, `title`, `session_date`, etc.; `corrected_value`: the field's plain value). |
| `wcs_*_additions` (four tables) | `POST /v1/wcs/admin/additions/*` | First-class operator-authored content not from any extraction. |

### Canonical layer (composition output, operator-editable per ADR-0006)

| Table | Purpose |
|---|---|
| `wcs_sources` | One row per lesson. Metadata sourced from the filename and (optionally) overridden by `source_metadata_corrections` or direct PATCH. |
| `wcs_instructors` | Canonical teaching authorities (people who taught a lesson or were attributed an attribution/definition). Includes Kaiano. |
| `wcs_instructor_aliases` | Naming surface for instructors. Globally-unique `alias` strings that resolve to a single canonical instructor. |
| `wcs_entities` | Canonical concepts, techniques, patterns, drills. Carries `kind`, `status`, `overview_md`. |
| `wcs_entity_aliases` | Naming surface for entities. |
| `wcs_source_attributions` | What an instructor taught about an entity in a source. The bulk of teaching content. |
| `wcs_entity_definitions` | Term/definition pairs attributed to a source and (optionally) an instructor. |
| `wcs_entity_relations` | Edges between entities (e.g. "prerequisite-of", "contrasts-with"). |
| `wcs_drill_purposes` | Skills a drill develops. |
| `wcs_technique_requirements` | Skills a technique requires. |
| `wcs_source_references` | People mentioned in a source, stored as raw names (not linked to canonical instructors — first-name-only mentions are too sparse to resolve). |

The canonical layer is **what every downstream reader sees**: the website, the wiki renderer, and (eventually) the Q&A agent.

---

## Corrections, additions, and direct edits

The substrate has three operator write surfaces. They have different jobs and coexist.

### Corrections (input-layer, ADR-0003)

Corrections are records that **override extraction interpretation at composition time**. They do not modify extraction output; they are applied as overlays when the Composition Service runs.

- **`name_corrections`** — change how `resolve_instructor` / `resolve_entity` interprets a name. Global scope: every source. Per-source scope: just one. Used when the LLM (or a filename) consistently mis-resolves a name and you want to fix it once for all current and future composition.
- **`attribution_corrections`** — override fields on individual attributions in a specific source.
- **`source_metadata_corrections`** — override filename-derived fields on a source (when the filename had a typo).

After a correction is added, the affected source needs a recompose: `POST /v1/wcs/admin/recompose/{source_id}`. The Composition Service re-runs, this time applying the correction.

**Use a correction when the fix should apply to resolution going forward** — including future ingests. A `name_correction` on `Robertroyston → Robert Royston` ensures every future source containing `Robertroyston` in its filename resolves to the canonical `robert-royston` row, not a new duplicate.

### Additions (input-layer, ADR-0003)

Additions create new content that doesn't come from any extraction:

- `attribution_additions` — operator-authored attributions.
- `drill_purpose_additions` — operator-authored drill purposes.
- `technique_requirement_additions` — operator-authored technique requirements.
- `entity_relation_additions` — operator-authored relations between entities.

Additions are first-class, not corrections to absent content. They carry `origin: manual` so downstream readers can distinguish them from extraction-derived content if desired (the wiki does not; the audit log does).

### Direct edits (canonical-layer, ADR-0006)

Direct edits **modify canonical rows in place**. They are the right tool for fixing data that is already wrong in the canonical layer and that you don't want to fix indirectly through a correction record.

- **`PATCH /v1/wcs/admin/sources/{id}`** — direct edit of source metadata fields. (Already exists.)
- **`PATCH /v1/wcs/admin/sources/{id}/visibility`** — direct edit of source visibility. (Already exists.)
- **`PATCH /v1/wcs/admin/instructors/{id}`** — direct edit of canonical name, slug, prose columns. (Per ADR-0006.)
- **`PATCH /v1/wcs/admin/entities/{id}`** — direct edit of canonical name, slug, kind, status, overview_md. (Per ADR-0006.)
- **`POST /v1/wcs/admin/instructors/{id}/repoint`** and **`/entities/{id}/repoint`** — move FK references from this row to another, for de-duplicating before deletion. (Per ADR-0006.)
- **`DELETE`** for both — hard delete when no live references remain. (Per ADR-0006.)
- **Alias add/remove** for both alias tables. (Per ADR-0006.)
- **Direct edits on per-source content rows** (attributions, definitions, relations, drill_purposes, technique_requirements, references) — also per ADR-0006. Re-extraction is rare-to-never, so the historical risk of "the edit gets overwritten" is not a practical concern.

Direct edits write to `wcs_admin_audit_log` (new table) with `operator_id`, `table`, `row_id`, `field`, `before`, `after`, `timestamp`, and an optional `reason`.

### Choosing between correction and direct edit

| Situation | Tool |
|---|---|
| Fix one canonical row's current state. | Direct edit. |
| Make a resolution rule apply to all future ingests too. | `name_correction` (global). |
| Both — fix the current row *and* prevent future duplicates from the same name variant. | Direct edit first, then `name_correction`. |
| Add a row that doesn't exist (an attribution that the LLM missed, a relation that should be recorded). | Addition. |
| Edit a transcript. | Not supported. (See "Transcript immutability" below.) |
| Re-run the LLM with an updated prompt. | Re-extraction. Rare-to-never (see "Re-extraction policy" below). |

---

## Transcript immutability

`wcs_transcripts.text` and `wcs_source_extractions.raw_output` are **not editable**. There is no operator endpoint for them; ADR-0006 explicitly preserves the immutability boundary at these tables.

The rationale:

- The transcript is a **historical record of what was said.** Editing it would rewrite history and create ambiguity about whether subsequent extractions reflect the recording or the operator's redaction.
- The extraction `raw_output` is a **historical record of what the LLM produced** for a specific (transcript, prompt-version, model) tuple. It is useful for prompt evaluation ("did the new prompt do better here?") and debugging. Editing it would destroy the historical signal.
- **Operator data-quality concerns are addressed downstream** in the canonical layer via direct edits and corrections. Whatever was wrong in the transcript or the extraction is fixed in the canonical state without modifying the input record.

If the transcript text turns out to contain a mishearing that materially distorts canonical state, the fix path is:

1. Edit the canonical row directly (per ADR-0006). The wrong word in the canonical prose is the immediate problem; fix it there.
2. (Optionally) add a `name_correction` if the mishearing is name-related and might recur in other transcripts.

The transcript itself is not modified.

---

## Re-extraction policy

**Re-extraction is opt-in per source and rare-to-never in practice.**

The pipeline supports it: a new extraction can be POSTed for an existing source, becomes the new active extraction (demoting the previous), and composition re-runs to derive a fresh canonical state. ADR-0003 designed the versioned-extractions table for exactly this case.

But this capability is not part of the operational rhythm. It is reserved for:

- **Prompt evolution.** The extraction prompt is improved in a way that meaningfully changes what gets extracted (a new section type, a better resolution heuristic, etc.).
- **A specific bug** where the LLM's output for a particular source is clearly degraded and re-running with the same prompt is expected to produce better output.

Even when the prompt changes, the default policy is **apply the new prompt only to new transcripts going forward.** Retroactive re-extraction of the existing corpus is theoretical; it has not happened and is not planned.

This is why operator data-quality work lives in the canonical layer (corrections and direct edits) rather than in extraction-time fixes. The substrate is designed to make canonical state durably correct without depending on re-extraction.

---

## Read surface

The substrate exposes three read shapes over canonical state. Each is served by a distinct endpoint group with distinct auth.

### Caller-scoped (per-user visibility)

Used by the website's `/notes/*` pages.

- `GET /v1/wcs/wiki/sources` — list visible sources for the caller.
- `GET /v1/wcs/wiki/sources/{id}` — one source view (visibility-checked).
- `GET /v1/wcs/wiki/entities/{slug}`, `/instructors/{slug}` — entity and instructor read views.

Visibility is computed by `visible_source_ids_for_user`: a source is visible to the caller iff it is `is_default_visible=true` or the caller has an explicit grant on it via `wcs_source_grants`.

### Admin-scoped (full corpus, human admin)

Used by the admin portal pages.

- `GET /v1/wcs/wiki/admin/sources` — full source list, no visibility filter.
- `GET /v1/wcs/wiki/admin/sources/{id}` — one source view, no visibility filter.

Gated on `require_scope("wcs.corpus.read")`, held by `wcs-admin` and `corpus-reader`. Deliberately not `wcs.notes.read`: that is the only scope in `wcs-reader`, the default role every human is provisioned with, so guarding a visibility-bypassing route on it grants the full corpus — private sources included — to every signed-in user.

### Service-scoped (full corpus, cog identity)

Used by the wiki renderer.

- `GET /v1/wcs/wiki/export` — bulk export of the entire canonical graph in one response.

Gated on `require_scope("wcs.corpus.read")`, held by the `corpus-reader` role. Not visibility-filtered — the renderer needs the full corpus to project the canonical graph as markdown, which is exactly why it does not map to `wcs.notes.read`: every `wcs-reader` holds that scope, and this endpoint bypasses per-source visibility. It replaced `require_wcs_service`, a token-type gate that granted access to any caller merely for being a machine.

---

## Consumers

### Website (`website-astro-wcs`)

SSR Astro site at `wcs.kaianolevine.com`. Reads the caller-scoped endpoints. Pages:

- `/notes` — visible sources, list.
- `/notes/[id]` — one source detail.
- `/admin/notes`, `/admin/notes/[id]` — source visibility/metadata admin (uses the admin-scoped endpoints).
- `/admin/*` (future) — the operator admin portal surfacing corrections, additions, direct edits, and gap discovery (per ADR-0002's deferred future work).

Authenticated via Clerk session JWT.

### Wiki renderer (`wiki-curator-cog`)

Stateless Prefect flow (per `wiki-curator-cog` ADR-002). One job: read the substrate, write markdown. Triggered on schedule or on demand.

Flow:

1. Authenticate to the API with the cog's own named API key (e.g. `TRANSCRIPTION_COG_API_KEY`); the key identifies the cog.
2. Clone or refresh the `wcs-wiki` repo.
3. `GET /v1/wcs/wiki/export` — fetch the full canonical graph.
4. Build in-memory indexes (entities by id and slug, instructors by id, sources by id, attributions grouped by entity and source, etc.).
5. Render the full markdown bundle per `wcs-wiki/CLAUDE.md` (v1.0 render spec): entity pages by kind (concepts/techniques/drills), source pages bucketed by primary instructor, instructor pages, four views, regenerated index.
6. Overwrite the wiki's derived pages wholesale, append one `log.md` entry, commit once (`render: <date> (<N> entities, <S> sources)`), push.

Given the same canonical state at the same render-spec version, output is byte-identical.

### Wiki repo (`wcs-wiki`)

The markdown projection of the canonical state. Browsable in Obsidian or on GitHub. Operator-readable, not operator-writable: hand-edits to derived pages are overwritten on next render. The editable surfaces are `CLAUDE.md` (the render spec), `README.md`, and the canonical store (via the API's admin endpoints).

### Q&A agent (future)

A retrieval-based agent at `wcs.kaianolevine.com/notes/ask`, designed in earlier sessions, currently paused. When built, it will read from canonical state via dedicated retrieval endpoints. Not part of the current pipeline but on the roadmap.

---

## Operator workflows

The common cleanup and curation workflows, mapped to which write surface they use.

### Fix a typo on a single canonical row

**Tool:** direct edit.
**Calls:** one `PATCH` on the affected canonical row.
**Example:** Rename instructor `Kristenwallace` → `Kristen Wallace`.

```
PATCH /v1/wcs/admin/instructors/{id}
{ "canonical_name": "Kristen Wallace", "slug": "kristen-wallace" }
```

### Collapse a duplicate canonical row

**Tool:** repoint + delete (direct edit primitives).
**Calls:** `POST /repoint` to move attributions and definitions, then `DELETE` on the orphan row. Optionally add the merged-away name as an alias on the canonical row so future ingests of the variant resolve correctly.
**Example:** Collapse `robertroyston` into `robert-royston`.

```
POST /v1/wcs/admin/instructors/{robertroyston-id}/repoint
{ "target_id": "{robert-royston-id}" }

POST /v1/wcs/admin/instructors/{robert-royston-id}/aliases
{ "alias": "Robertroyston" }

DELETE /v1/wcs/admin/instructors/{robertroyston-id}
```

For broader protection against future re-creation of the duplicate, also add a global name correction.

```
POST /v1/wcs/admin/corrections/name
{ "raw_name": "Robertroyston", "corrected_name": "Robert Royston", "scope": "global" }
```

### Fix a wrong filename on a source

**Tool:** source metadata correction (it recomposes the source itself).
**Calls:** `POST /v1/wcs/admin/corrections/metadata`.
**Example:** Source's filename had `Kaiano-Amy` as a single concatenated instructor; correct to two separate names.

```
POST /v1/wcs/admin/corrections/metadata
{ "source_id": "...", "field": "instructors", "corrected_value": ["Kaiano", "Amy"], "reason": "Filename joined two names." }
```

`field` is one of `title`, `organization`, `session_date`, `session_type`, `instructors`, `students`, `visibility`, `is_default_visible`. `corrected_value` is that field's plain value: a string (`session_type` one of `private_lesson`, `group_class`, `other`; `visibility` one of `private`, `public`), an ISO date string for `session_date`, a boolean for `is_default_visible`, a list of names for `instructors` and `students`. A value that does not fit its field is a 422 and nothing is written.

After recompose, the synthetic `kaiano-amy` instructor row is orphan. `DELETE` it.

### Add a relation between entities the LLM missed

**Tool:** addition.
**Calls:** `POST /v1/wcs/admin/additions/entity_relation`.
**Example:** The LLM didn't capture that `sugar-tuck` is a variation-of `sugar-push`.

### Configure resolution to handle a name variant globally

**Tool:** name correction (global scope).
**Calls:** `POST /v1/wcs/admin/corrections/name` with `scope: global`.
**Example:** Future-proof against `RobertRoyston` (no space) resolving to a new duplicate row.

### Re-render the wiki after corrections

**Tool:** trigger the renderer.
**Calls:** invoke the `export` Prefect flow on `wiki-curator-cog`. (Or wait for the next scheduled run.)
**Effect:** the markdown bundle in `wcs-wiki` regenerates from current canonical state.

---

## What's not in this pipeline

These are explicitly out-of-scope and worth naming so future work knows where the boundary is.

- **Transcript editing.** Not supported. See "Transcript immutability" above. If the need ever arises, it would be a meaningful design extension; the current model rules it out.
- **Retroactive re-extraction.** Capable but not used. See "Re-extraction policy."
- **In-place mutation of `wcs_source_extractions.raw_output`.** Not supported. The blob is preserved for evaluation and debugging.
- **Operator UI in `wcs-wiki`.** The wiki is read-only from an operator perspective; edits go to the API.
- **Inter-cog direct DB access.** All cogs are API clients. The substrate is owned by `api-kaianolevine-com`.

---

## Cross-references

- The ingest cog: `transcription-cog/src/transcription_cog/flow.py`, function `process_transcript`.
- The Composition Service: `api-kaianolevine-com/src/kaianolevine_api/services/wcs_composition.py`.
- Admin endpoints: `api-kaianolevine-com/src/kaianolevine_api/routers/wcs_admin.py`.
- Visibility logic: `api-kaianolevine-com/src/kaianolevine_api/services/wcs_source_visibility.py`.
- Wiki export endpoint: `api-kaianolevine-com/src/kaianolevine_api/routers/wcs_wiki.py`, function `export_wiki`.
- Renderer: `wiki-curator-cog/src/wiki_curator_cog/render.py`.
- Render spec: `wcs-wiki/CLAUDE.md` (v1.0).
- Website notes UI: `website-astro-wcs/src/pages/notes/`.
- ADRs: `api-kaianolevine-com/docs/decisions/`, `wiki-curator-cog/docs/decisions/`.
