# WCS Q&A Agent

The Q&A agent answers questions about the WCS lesson corpus with inline citations. It is implemented under `src/kaianolevine_api/agents/wcs_qa/` with retrieval in `src/kaianolevine_api/retrieval/wcs/` and HTTP routes in `src/kaianolevine_api/routers/wcs_qa.py`.

## HTTP surface

Routes are mounted at `/v1` (see `main.py`):

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| `POST` | `/v1/wcs/embeddings/refresh` | `wcs.embeddings.write` (`wcs-admin`, `wcs-writer`) | Run the embedding convergence flow synchronously; returns counts of notes/transcripts embedded vs skipped. |
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

v1 retrieval reads the **legacy notes + transcripts tables**, not the entity substrate (`wcs_sources`, attributions, etc.):

- **Notes:** `LegacyWcsNote` → table `_legacy_wcs_notes` (renamed from `wcs_notes` in migration 019). Structured `notes_json` from LLM extraction.
- **Transcripts:** `WcsTranscript` → `wcs_transcripts`, chunked into `wcs_transcript_chunks` for search.

ADR-0002 describes a future move to entity-grain retrieval tools; that migration is not implemented in the Q&A agent yet.

## Retrieval surface

The agent exposes exactly **four tools** to the model (`agents/wcs_qa/loop.py` → `retrieval/wcs/tools.py`):

### `search_notes(query, k?, filters?)`

- Embeds `query` via OpenAI, then **pgvector cosine distance** over `wcs_note_embeddings` joined to `_legacy_wcs_notes` (Postgres).
- Returns up to `k` hits (default 10, max 25) with title, session metadata, snippet (~200 chars of flattened note text), and similarity `score`.
- **No `source_url` on hits** — the model must call `get_note` before citing.
- **Visibility:** same as the rest of WCS notes — default-visible OR admin OR explicit `wcs_note_grants` grant (`user_can_see_note`).
- **Filters:** `date_from`, `date_to`, `instructors`, `session_type`, `organization`.

### `search_transcripts(query, k?, filters?)`

- Vector search over `wcs_transcript_chunks` joined to `wcs_transcripts` (and optionally linked notes for metadata filters).
- Each hit includes `chunk_id` as `"<transcript_uuid>:<chunk_index>"`, linked note title/date when available, and a snippet.
- **No `source_url` on hits** — use `get_transcript_window` before citing.
- **Visibility:** **owner-scoped** — only chunks whose `owner_id` matches the authenticated caller. No grant model for transcripts in v1.
- **Filters:** `date_from`, `date_to`, `instructors` (via linked note).

### `get_note(note_id)`

- Loads full structured note (`notes_json`) and `source_url` (`{WCS_SITE_URL}/notes/{uuid}`).
- Returns `not_found` (via `ToolError`) if the note is missing or not visible to the viewer — no existence leak.

### `get_transcript_window(chunk_id, before?, after?)`

- Fetches consecutive chunks around `chunk_id` (defaults: 1 before, 1 after).
- Owner must match the transcript chunk's `owner_id`.
- `source_url` in the window is set only when the transcript has exactly one linked legacy note.

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
  {"marker": 1, "type": "note", "id": "<note-uuid>"},
  {"marker": 2, "type": "chunk", "id": "<transcript-uuid>:<chunk_index>"}
]
[[CITATIONS_END]]
```

Parsing is in `agents/wcs_qa/citations.py` (`SENTINEL_RE`, `parse_citations_block`). Valid `type` values: `note`, `chunk`. The API strips the entire sentinel block from `answer` and returns enriched metadata per marker (`title`, `session_date`, instructors/students/organization, `source_url` where applicable).

Chunk citations get `source_url` only when the transcript has exactly one linked legacy note; otherwise `source_url` is null in v1.

## Evaluation

Offline eval lives under `tests/evals/` (not part of normal CI unless keys are present).

**Question set:** `tests/evals/questions.yaml` — each entry has `id`, `question`, `ideal_answer`, and `ideal_source_ids` (`notes` / `chunks` UUID lists for metric computation).

**Harness:** `tests/evals/test_harness.py`, run locally with:

```bash
doppler run -- pytest tests/evals/
```

Requires `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and a non-empty question set. For each question:

1. `POST /v1/wcs/ask` via in-process ASGI (`httpx` + dependency override on `get_current_owner`; user defaults to `WCS_QA_EVAL_USER_ID` or `dev-owner`).
2. **Auto-metrics** (`tests/evals/metrics.py`): `source_recall` and `source_precision` comparing cited IDs to `ideal_source_ids`.
3. **LLM-as-judge** (`tests/evals/judge.py`): separate model (`WCS_QA_JUDGE_MODEL`, default `claude-opus-4-7`) scores the agent answer 1–5 against `ideal_answer` using `JUDGE_PROMPT` (agent runs on Sonnet — cross-model grading).
4. **Persistence:** one row per `(run_id, question_id)` in `wcs_qa_eval_runs` (migration 018), including `git_sha`, `agent_answer`, `cited_source_ids`, `tool_trace`, metrics, judge score/reasoning, and `judge_prompt_sha` (SHA-256 of `JUDGE_PROMPT` for drift detection).

`manual_grade` / `manual_grade_notes` columns are reserved for a future admin UI; the harness leaves them NULL.

Unit tests for the agent and retrieval (no live APIs) are in `tests/integration/test_wcs_qa.py`, `tests/integration/test_loop.py`, and `tests/integration/test_retrieval.py`.

## Embeddings

Schema: migration `017_wcs_qa_embeddings.sql`.

| Table | Grain | Key |
| ----- | ----- | --- |
| `wcs_note_embeddings` | One row per `(note_id, embedding_model, flattener_version)` | Embeds flattened note text |
| `wcs_transcript_chunks` | One row per chunk × `(embedding_model, chunking_version)` | Chunk text + offsets + embedding |

Vectors are **1536-dimensional** (`vector` extension). v1 uses **exact cosine search** (no ANN index) — acceptable at current corpus size.

**Population:** `refresh_embeddings` (`retrieval/wcs/convergence.py`), invoked by `POST /v1/wcs/embeddings/refresh`:

- Computes canonical text per source (notes via `flatten_note` / `FLATTENER_VERSION`; transcripts via `chunk_transcript` / `chunking_version`).
- Stores `content_sha`; re-embeds only when the hash changes (idempotent).
- Transcripts: if SHA mismatches, deletes existing chunks for that transcript at the current config and re-chunks + re-embeds.

Config knobs: `WCS_QA_EMBEDDING_MODEL`, `WCS_QA_FLATTENER_VERSION`, `WCS_QA_CHUNKING_VERSION` (see `config.py`).

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
