# WCS Q&A Agent

The Q&A agent answers questions about the WCS lesson corpus with inline citations. It is implemented under `src/kaianolevine_api/agents/wcs_qa/` with retrieval in `src/kaianolevine_api/retrieval/wcs/` and HTTP routes in `src/kaianolevine_api/routers/wcs_qa.py`.

## HTTP surface

Routes are mounted at `/v1` (see `main.py`):

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| `POST` | `/v1/wcs/embeddings/refresh` | `wcs.embeddings.write` (`wcs-admin`, `wcs-writer`) | Run the embedding convergence flow synchronously; returns counts of transcripts and wiki pages embedded vs skipped. |
| `POST` | `/v1/wcs/ask` | `wcs.notes.read` (every human, via `wcs-reader`) | Single-turn Q&A: run the agent loop and return answer + enriched citations + usage/cost. Retrieval is visibility-filtered per caller. |

**Ask request body:** `{ "question": "<string, 1–5000 chars>" }` — no conversation history in v1.

**Ask response (`data`):**

- `answer` — model text with the internal citation sentinel block removed; inline `[1]`, `[2]`, … markers remain.
- `citations` — DB-validated, visibility-filtered `EnrichedCitation` objects (may be empty).
- `budget_exhausted` — true if token or tool-call caps forced a final no-tools turn.
- `tool_trace_id` — hex id for logs and eval correlation.
- `usage` — `{ model, input_tokens, output_tokens, cost_usd }` (cost is an estimate from `agents/wcs_qa/pricing.py`; embedding cost on the question is excluded).

**Frontend integration:** The product UI is at [wcs.kaianolevine.com/notes/ask](https://wcs.kaianolevine.com/notes/ask). A planned admin eval UI is referenced at `/notes/ask/eval` in migration 018; manual grade fields exist on `wcs_qa_eval_runs` for that future surface.

## Corpus and scope

Retrieval reads the **lessons** and the **wiki**:

- **Lessons:** `WcsSource` → `wcs_sources`. Each lesson's transcript (`WcsTranscript` → `wcs_transcripts`) is chunked into `wcs_transcript_chunks` for search. A lesson's title, date, type, instructors, students and organization come from the source, and its page on the site is `/notes/detail?id=<source id>`.
- **Wiki pages:** `WcsWikiPageRow` → `wcs_wiki_pages`, the page documents wiki-curator-cog builds, embedded into `wcs_wiki_page_embeddings` (migration 063).

ADR-0002 describes a future move to entity-grain retrieval tools; that migration is not implemented in the Q&A agent yet.

## Retrieval surface

The agent exposes **four tools** to the model (`agents/wcs_qa/loop.py` → `retrieval/wcs/tools.py`, and `retrieval/wcs/wiki_pages.py` for the two over the wiki):

### `search_wiki(query, k?)` and `get_wiki_page(name)`

- `search_wiki` is vector search over page embeddings (default k 5, max 15); hits carry slug, name, kind and the start of the definition, no URL.
- `get_wiki_page` reads one page by slug, display name or other name (a slug folded into a page serves that page). It returns the definition, In practice, details, mistakes, related pages, teachers and `source_url` (`/wiki/page?slug=`).
- Visibility is the /wiki routes' rule (`services/wcs_wiki_pages.py`): a page with no evidence, or with a source the asker can see, links to hidden sources reduced to their label. An admin sees every page, as on /admin/wiki.
- The prompt starts questions about an idea at the wiki, then backs the answer with lessons, and answers first with the pages numbered ahead of the lessons.

### `search_transcripts(query, k?, filters?)`

- Vector search over `wcs_transcript_chunks`, joined to `wcs_transcripts` and each transcript's lesson (`wcs_sources`).
- Each hit includes `chunk_id` as `"<transcript_uuid>:<chunk_index>"`, the lesson's `source_id`, title, date, type, instructors, students and organization, and a snippet.
- **No `source_url` on hits** — use `get_transcript_window` before citing.
- **Filters:** `date_from`, `date_to`, `instructors`, read from the lesson.

### `get_transcript_window(chunk_id, before?, after?)`

- Fetches consecutive chunks around `chunk_id` (defaults: 1 before, 1 after), with the lesson they belong to and its `source_url`.
- Returns `not_found` (via `ToolError`) when the chunk is missing or its lesson is not visible to the asker — no existence leak.

### Visibility

A lesson is visible exactly as the wiki decides it (`services/wcs_source_visibility.visible_sources_clause`): every lesson for an asker whose roles carry `wcs.corpus.read`; otherwise the default-visible lessons and those granted to the asker (`wcs_source_grants`). The chunks' `owner_id` is still written but never decides visibility — every chunk is owned by the transcription cog's machine principal, so an owner filter would match no person.

There is **no hybrid RRF or keyword fusion** in v1 — retrieval is embedding similarity only.

## Agent loop

`run_agent` (`agents/wcs_qa/loop.py`) runs a standard Anthropic tool-use loop:

1. Model may call retrieval tools until `stop_reason == "end_turn"` or budgets hit.
2. **Budgets** (from `AgentConfig` / settings): cumulative input tokens, max tool calls, max output tokens per turn. Exceeding caps triggers a final user message (`EXHAUSTION_MESSAGE`) and one no-tools completion.
3. **Citation parse:** the final text must contain a sentinel JSON block (see below). On failure, one corrective no-tools retry (`CORRECTIVE_RETRY_MESSAGE`). A second failure returns the answer with `citations: []` and `citation_parse_failed` logged internally (not exposed on the HTTP response today).
4. **Enrichment:** parsed IDs are validated against the DB and visibility rules (`agents/wcs_qa/citations.py`).

Default models (config): agent `claude-sonnet-4-6`, embeddings `text-embedding-3-small`.

## Citation format

The system prompt (`agents/wcs_qa/prompts.py`) requires:

1. **Inline markers** `[1]`, `[2]`, … in the prose (order of first appearance).
2. A **sentinel-delimited JSON block** at the end of the model output:

```
[[CITATIONS_BEGIN]]
[
  {"marker": 1, "type": "page", "id": "<page-slug>"},
  {"marker": 2, "type": "lesson", "id": "<source-uuid>"},
  {"marker": 3, "type": "chunk", "id": "<transcript-uuid>:<chunk_index>"}
]
[[CITATIONS_END]]
```

Parsing is in `agents/wcs_qa/citations.py` (`SENTINEL_RE`, `parse_citations_block`). Valid `type` values: `page`, `lesson`, `chunk`. A lesson citation names a lesson by its source id; a chunk citation names a passage the agent read. Both carry the lesson's metadata and link to its page; one whose lesson the asker may not see, or a chunk whose transcript has no lesson, is dropped. A page citation carries the page's name as `title`, its `kind`, and its /wiki `source_url`; one the asker may not see is dropped. The API strips the entire sentinel block from `answer` and returns enriched metadata per marker (`title`, `session_date`, instructors/students/organization, `source_url` where applicable).

## Evaluation

Offline eval lives under `tests/evals/` (not part of normal CI unless keys are present).

**Question set:** `tests/evals/questions.yaml` — each entry has `id`, `question`, `ideal_answer`, and `ideal_source_ids` (`lessons` source UUIDs, `chunks` ids and `pages` slugs, for metric computation; runs recorded earlier carry `notes`).

**Harness:** `tests/evals/test_harness.py`, run locally with:

```bash
RUN_EVALS=1 doppler run -- pytest tests/evals/
```

Requires `RUN_EVALS=1`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and a non-empty question set. `doppler.yaml` pins local runs to the `dev` config, so rows land in the development database. The harness refuses to collect when the process resolves as production (CD-033) — there is no supported way to point it at production. For each question:

1. `POST /v1/wcs/ask` via in-process ASGI (`httpx` + dependency override on `get_current_owner`; user defaults to `WCS_QA_EVAL_USER_ID` or `dev-owner`).
2. **Auto-metrics** (`tests/evals/metrics.py`): `source_recall` and `source_precision` comparing cited IDs to `ideal_source_ids`.
3. **LLM-as-judge** (`tests/evals/judge.py`): separate model (`WCS_QA_JUDGE_MODEL`, default `claude-opus-4-7`) scores the agent answer 1–5 against `ideal_answer` using `JUDGE_PROMPT` (agent runs on Sonnet — cross-model grading). It is also given the text of every cited source (`tests/evals/sources.py`) and says whether each claim in the answer is backed by one (`judge_grounded`).
4. **Persistence:** one row per `(run_id, question_id)` in `wcs_qa_eval_runs` (migration 018), including `git_sha`, `agent_answer`, `cited_source_ids`, `tool_trace`, metrics, judge score/reasoning/`judge_grounded`, and `judge_prompt_sha` (SHA-256 of `JUDGE_PROMPT` for drift detection).

`manual_grade` / `manual_grade_notes` columns are reserved for a future admin UI; the harness leaves them NULL.

Unit tests for the agent and retrieval (no live APIs) are in `tests/integration/test_wcs_qa.py`, `tests/integration/test_loop.py`, and `tests/integration/test_retrieval.py`.

## Embeddings

Schema: migration `017_wcs_qa_embeddings.sql`.

| Table | Grain | Key |
| ----- | ----- | --- |
| `wcs_transcript_chunks` | One row per chunk × `(embedding_model, chunking_version)` | Chunk text + offsets + embedding |

Vectors are **1536-dimensional** (`vector` extension). v1 uses **exact cosine search** (no ANN index) — acceptable at current corpus size.

**Population:** `refresh_embeddings` (`retrieval/wcs/convergence.py`). It runs by itself after content changes (`retrieval/wcs/auto_refresh.py`): storing a transcript or a note, an admin edit to a note, or a page write that created, changed or removed a wiki page requests a refresh, which runs off the request path after `WCS_QA_REFRESH_DEBOUNCE_SECS` of quiet, so one lesson's writes give one refresh. Every start requests one too, for anything a stopped process left. A failed refresh is retried, then reported to Discord's errors channel and Sentry. A Postgres advisory lock keeps refreshes one at a time across processes. `POST /v1/wcs/embeddings/refresh` still runs one on demand.

- Computes canonical text per source (transcripts via `chunk_transcript` / `chunking_version`, each chunk embedded under its lesson's title, or the transcript's filename when the lesson has none; wiki pages via `wiki_pages.flatten_page` / `page_flattener_version`).
- The legacy notes are no longer embedded: their table is empty and nothing searches it. `wcs_note_embeddings` remains in the schema, unused.
- Stores `content_sha`; re-embeds only when the hash changes (idempotent).
- Transcripts: if SHA mismatches, deletes existing chunks for that transcript at the current config and re-chunks + re-embeds.

Config knobs: `WCS_QA_EMBEDDING_MODEL`, `WCS_QA_CHUNKING_VERSION`, `WCS_QA_PAGE_FLATTENER_VERSION`, `WCS_QA_REFRESH_DEBOUNCE_SECS` (listed in `.env.example`; defaults in `config.py`).

During `ask`, the user's question is embedded on each `search_*` call; there is no separate index refresh in the ask path.

## Related files

| Area | Path |
| ---- | ---- |
| Agent loop | `src/kaianolevine_api/agents/wcs_qa/loop.py` |
| Citations | `src/kaianolevine_api/agents/wcs_qa/citations.py` |
| Prompts | `src/kaianolevine_api/agents/wcs_qa/prompts.py` |
| Router | `src/kaianolevine_api/routers/wcs_qa.py` |
| Retrieval tools | `src/kaianolevine_api/retrieval/wcs/tools.py` |
| DB queries | `src/kaianolevine_api/retrieval/wcs/queries.py` |
| Convergence | `src/kaianolevine_api/retrieval/wcs/convergence.py` |
