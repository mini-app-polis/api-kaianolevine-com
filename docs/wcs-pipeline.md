# WCS pipeline — the API's part

The West Coast Swing pipeline runs from a recorded lesson in Google Drive, through `watcher-cog` and `transcription-cog`, into this API's substrate, and out to the website and the wiki. The end-to-end architecture, with every stage and the repository that runs it, is in [`ecosystem-standards`' `docs/wcs-pipeline.md`](https://github.com/mini-app-polis/ecosystem-standards/blob/main/docs/wcs-pipeline.md).

This document covers what this API owns: the two ingest calls, the substrate they write, the corrections and edits made on top of it, the read surface, and the operator workflows.

The ADRs behind it:

- **ADR-0002**: the WCS entity substrate.
- **ADR-0003**: versioned extractions and corrections.
- **ADR-0004**: the rebuild from scratch that produced the current substrate.
- **ADR-0006**: operator direct edits on canonical state.

---

## Ingest: the two calls

`transcription-cog`'s worker calls the API twice for each file. It stores the transcript once it has the text, and it stores the source once the LLM extraction is done.

### Transcript persistence

`POST /v1/wcs/transcripts` creates a row in `wcs_transcripts` with the raw text, the `drive_file_id`, the inferred `source_type` and an auto-assigned UUID. The endpoint is **idempotent on `drive_file_id`** (unique constraint `uq_wcs_transcripts_drive_file_id`). Ingesting a file again with the same Drive id returns the existing row.

The result is a **`transcript_id`**. This row is the substrate's permanent record of *what was said*, and it is **immutable** from the moment it lands. There is no operator surface for editing transcript text. (See ADR-0006 for the rationale.)

### Source store and composition

`POST /v1/wcs/sources` (the cog's `task_store_source`) does the following in one synchronous call:

1. Creates or updates the `wcs_sources` row with the filename-derived metadata. The filename is authoritative for instructors, students, organization, date and topic.
2. Writes the extraction as a new active row in `wcs_source_extractions`. Any extraction that was active for this source is marked inactive but not deleted, because extractions are versioned and kept.
3. Runs the **Composition Service** (`services/wcs_composition.py`) synchronously. It derives all the canonical-layer rows for this source:
   - Resolves each `instructors_raw` and `students_raw` name through `resolve_instructor`, producing `wcs_instructors` rows. It creates new rows, finds existing ones by slug or alias, and applies any matching `wcs_name_corrections`.
   - Resolves each entity reference in the extraction through `resolve_entity`, producing `wcs_entities` rows.
   - Writes the per-source content rows: `wcs_source_attributions`, `wcs_entity_definitions`, `wcs_entity_relations`, `wcs_drill_purposes`, `wcs_technique_requirements` and `wcs_source_references`.
4. Returns the refreshed `WcsSourceItem` only after all canonical rows are committed.

There is **no eventual consistency**. The canonical layer is current the instant the call returns.

Then, if `flags.wcs.wiki_jobs_enabled` is on, the API queues a wiki `link` job for the source on `wiki-curator-jobs.fifo` (`services/wiki_dispatch.py`). The flag was seeded off in migration 055. A dropped job is reported to the errors channel and never fails the ingest.

The result is the **canonical state** of the substrate for that source.

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

Direct edits **modify canonical rows in place**. Two exist today:

- **`PATCH /v1/wcs/admin/sources/{id}`** — direct edit of source metadata fields.
- **`PATCH /v1/wcs/admin/sources/{id}/visibility`** — direct edit of source visibility.

**Planned (ADR-0006 Pass 1), not built.** None of these routes exist; calling them is a 404:

- `PATCH /v1/wcs/admin/instructors/{id}` and `PATCH /v1/wcs/admin/entities/{id}` — edit a canonical row's name, slug and prose.
- `POST /v1/wcs/admin/instructors/{id}/repoint` and `/entities/{id}/repoint` — move references to another row before deleting.
- `DELETE` on instructors and entities, and alias add/remove on both alias tables.
- Direct edits on per-source content rows (attributions, definitions, relations, drill purposes, technique requirements, references).
- A `wcs_admin_audit_log` table recording each direct edit.

Until they exist, canonical rows are fixed through corrections and recompose, and duplicate entities are merged through notes (see "Operator workflows"). A canonical row left with no references has no route to delete it.

### Choosing between correction and direct edit

| Situation | Tool |
|---|---|
| Fix a wrong name on an instructor or entity. | `name_correction`, then recompose the affected sources. |
| Make a resolution rule apply to all future ingests too. | `name_correction` (global). |
| Fix a wrong filename-derived field on a source. | `source_metadata_correction` (it recomposes the source). |
| Two entities are the same thing. | A note saying so: the extraction records `alias_of`, and the wiki merges them at render. |
| Add a row that doesn't exist (an attribution that the LLM missed, a relation that should be recorded). | Addition. |
| Edit a canonical row directly, or delete one. | Not available yet (planned direct edits, above). |
| Edit a transcript. | Not supported. (See "Transcript immutability" below.) |
| Re-run the LLM with an updated prompt. | Re-extraction. Rare-to-never (see "Re-extraction policy" below). |

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
- `GET /v1/wcs/wiki/{concepts|techniques|patterns|drills}/{slug}`, `/instructors/{slug}` (and their unslugged list forms) — entity and instructor read views.

- `GET /v1/wcs/wiki/pages` and `/v1/wcs/wiki/pages/{slug}` — the wiki's page documents (wiki-curator-cog ADR-0006), filtered to the evidence the caller can see. Statements, mistakes, skills and relations from a hidden source are dropped and a link to one keeps only its label; a page built only from hidden sources is a 404.

Visibility is computed by `visible_source_ids_for_user`: a source is visible to the caller iff it is `is_default_visible=true` or the caller has an explicit grant on it via `wcs_source_grants`. For the wiki pages it is read on every request, never from the stored document, so a source made private disappears from its pages at once.

### Admin-scoped (full corpus, human admin)

Used by the admin portal pages.

- `GET /v1/wcs/wiki/admin/sources` — full source list, no visibility filter.
- `GET /v1/wcs/wiki/admin/sources/{id}` — one source view, no visibility filter.
- `GET /v1/wcs/wiki/admin/pages` and `/v1/wcs/wiki/admin/pages/{slug}` — the wiki's page documents whole, each citation marked with its source's current visibility; `?as=public` shows what a reader with no grants would see.
- `GET /v1/wcs/wiki/admin/revisions/sources`, `/v1/wcs/wiki/admin/revisions` and `/v1/wcs/wiki/admin/revisions/{id}` — what each source changed in the wiki: revisions counted per origin source (`source_id` null for admin and operator work), listed newest first (`?source_id=<uuid>|none&slug=`), and one with its documents before and after, unfiltered. Every page write records a revision for each page it creates, changes or removes, in `wcs_wiki_page_revisions` (migration 060; kept indefinitely for now).

Gated on `require_scope("wcs.corpus.read")`, held by `wcs-admin` and `corpus-reader`. Deliberately not `wcs.notes.read`: that is the only scope in `wcs-reader`, the default role every human is provisioned with, so guarding a visibility-bypassing route on it grants the full corpus — private sources included — to every signed-in user.

### Service-scoped (full corpus, cog identity)

Used by the wiki renderer.

- `GET /v1/wcs/wiki/export` — bulk export of the entire canonical graph in one response. Built once per revision of the corpus and kept in memory (below).

- `GET` / `POST /v1/wcs/wiki/state/{links,facets,prose}` — the curator's state (references, facet decisions, prose and revisions), keyed as its files were; read with `wcs.corpus.read`, written with `wcs.wiki.write` (role `wiki-writer`).
- `GET` / `POST /v1/wcs/wiki/state/thin` — the thin entities the curator finds, for the admin's review (`wcs_wiki_thin`). A `POST` replaces each entity given and deletes the slugs in `remove`; same scopes as the state above.
- `POST /v1/wcs/wiki/jobs` — enqueue wiki jobs; the worker sends a job's follow-ons here (`wcs.wiki.write`).
- `POST /v1/wcs/wiki/admin/jobs` — the /admin/wiki panel starts a free operation: rebuild every page, or named pages (`wcs.wiki.operate`, held by `wcs-admin`). The same request again within five minutes queues nothing, because SQS drops it as a duplicate. The response then says `deduplicated: true` and gives the first send's `queued_at`, so the panel watches the job that is already running.
- `POST /v1/wcs/wiki/admin/notes` — a note written on a wiki page: `{date, instructor, title, text}` goes on transcription-cog's queue as a `wcs-note` message, and the cog processes it as `<date> <instructor> > Note - <title>` from Drive would be, storing a correction source. Paid (one extraction, then the wiki's passes), so it has its own scope, `wcs.notes.author`, held by `wcs-admin` (migration 061). The note's id comes from its content and is its transcript's `drive_file_id`, so the same note sent twice is one transcript.
- `POST /v1/wcs/wiki/pages` — the curator's `materialise` step stores page documents here and removes pages folded away (`wcs.wiki.write`). Every page sent is written, so its `updated_at` moves. The answer names which pages were `created`, which `changed` compared with what they replaced, and which were removed (`removed_slugs`), so the curator's run report can say what a rebuild changed. The write's optional `origin_source_id` and `origin_job_id` are recorded on each revision. The API stores, filters and serves them; it never recomputes a page.

The export is gated on `require_scope("wcs.corpus.read")`, held by the `corpus-reader` role. Not visibility-filtered — the renderer needs the full corpus to project the canonical graph as markdown, which is exactly why it does not map to `wcs.notes.read`: every `wcs-reader` holds that scope, and this endpoint bypasses per-source visibility. It replaced `require_wcs_service`, a token-type gate that granted access to any caller merely for being a machine.

**Built once per revision.** The export is about 30 MB and takes 15–20 s to build, and every curator job reads it. On 2026-10-18, five jobs read it at once, each building its own copy on the one process that serves every route, and the API stopped answering. Running fewer jobs at once made that less likely but didn't make a read any cheaper. Now:

- Migration 058 keeps a corpus revision in `wcs_corpus_revision`. Triggers on every table the export reads bump it once per writing transaction, at commit, whatever the write path.
- The response carries an ETag made from that revision and the deployed commit, so a deploy that changes the output never answers 304 for the old body. A caller that sends it back as `If-None-Match` gets a 304 for one small query, even right after a restart.
- The serialised body is kept in memory per revision (`services/wcs_wiki_export.py`). Readers that arrive during a build wait for it instead of starting another. Past a few waiting, the answer is 503 with `Retry-After`.
- `KaianoApiClient.export_wcs_wiki()` sends the ETag and keeps the last body, so a warm curator Lambda gets 304s while the corpus is unchanged. It backs off before retrying.

Before adding curator concurrency that would read the export in parallel, check that these still hold: one build per revision, and parallel readers wait on it.

---

## Operator workflows

The common cleanup and curation workflows, mapped to which write surface they use.

### Fix a typo in an instructor's name

**Tool:** name correction, then recompose.
**Calls:** `POST /v1/wcs/admin/corrections/name`, then `POST /v1/wcs/admin/recompose/{source_id}` for each source the name appears in. A global correction defers the recompose, so run it yourself.
**Example:** Sources resolve `Kristenwallace` instead of `Kristen Wallace`.

```
POST /v1/wcs/admin/corrections/name
{ "raw_name": "Kristenwallace", "corrected_name": "Kristen Wallace", "scope": "global" }

POST /v1/wcs/admin/recompose/{source_id}
```

### Collapse a duplicate

**Instructors.** A name correction from the duplicate's spelling to the canonical name, then a recompose of each source that used it, moves those sources' attributions onto the canonical row and stops future ingests from creating the duplicate again.

```
POST /v1/wcs/admin/corrections/name
{ "raw_name": "Robertroyston", "corrected_name": "Robert Royston", "scope": "global" }

POST /v1/wcs/admin/recompose/{source_id}
```

The duplicate row stays in the database with no references: deleting it needs the planned `DELETE` route.

**Entities.** Write a note saying the two names are the same thing and which to use ("underarm turn is the same pattern as right side pass with underarm turn"). Its extraction records an `alias_of` relation, and the wiki merges the two at render: the preferred page absorbs the other's content and lists its name as an alias. Nothing changes in the database, so a later note saying they are distinct splits them again.

### Fix a wrong filename on a source

**Tool:** source metadata correction (it recomposes the source itself).
**Calls:** `POST /v1/wcs/admin/corrections/metadata`.
**Example:** Source's filename had `Kaiano-Amy` as a single concatenated instructor; correct to two separate names.

```
POST /v1/wcs/admin/corrections/metadata
{ "source_id": "...", "field": "instructors", "corrected_value": ["Kaiano", "Amy"], "reason": "Filename joined two names." }
```

`field` is one of `title`, `organization`, `session_date`, `session_type`, `instructors`, `students`, `visibility`, `is_default_visible`. `corrected_value` is that field's plain value: a string (`session_type` one of `private_lesson`, `group_class`, `other`; `visibility` one of `private`, `public`), an ISO date string for `session_date`, a boolean for `is_default_visible`, a list of names for `instructors` and `students`. A value that does not fit its field is a 422 and nothing is written.

After recompose, the synthetic `kaiano-amy` instructor row has no references. It stays in the database until the planned `DELETE` route exists.

### Add a relation between entities the LLM missed

**Tool:** addition.
**Calls:** `POST /v1/wcs/admin/additions/entity_relation`.
**Example:** The LLM didn't capture that `sugar-tuck` is a variation-of `sugar-push`.

### Configure resolution to handle a name variant globally

**Tool:** name correction (global scope).
**Calls:** `POST /v1/wcs/admin/corrections/name` with `scope: global`.
**Example:** Future-proof against `RobertRoyston` (no space) resolving to a new duplicate row.

### Re-render the wiki after corrections

**Tool:** nothing, once the correction is composed: the source's `link` job and its follow-ons regenerate and re-materialise the pages it touches.
**By hand:** `python -m wiki_curator_cog.main materialise` (every page, or `--entity`) rebuilds the stored page documents; `render` writes the markdown export.

---

## What's not in this pipeline

These are explicitly out-of-scope and worth naming so future work knows where the boundary is.

- **Transcript editing.** Not supported. See "Transcript immutability" above. If the need ever arises, it would be a meaningful design extension; the current model rules it out.
- **Retroactive re-extraction.** Capable but not used. See "Re-extraction policy."
- **In-place mutation of `wcs_source_extractions.raw_output`.** Not supported. The blob is preserved for evaluation and debugging.

---

## Cross-references

- The Composition Service: `src/kaianolevine_api/services/wcs_composition.py`.
- Admin endpoints: `src/kaianolevine_api/routers/wcs_admin.py`.
- Visibility logic: `src/kaianolevine_api/services/wcs_source_visibility.py`.
- Wiki export endpoint: `src/kaianolevine_api/routers/wcs_wiki.py`, function `export_wiki`.
- The Q&A agent: `docs/wcs-qa-agent.md`.
- The end-to-end pipeline, and who reads what: `ecosystem-standards`' `docs/wcs-pipeline.md`.
- ADRs: `docs/decisions/`.
