"""Tool-use loop for the WCS Q&A agent.

Standard Anthropic SDK pattern: send messages with tools, dispatch tool_use
blocks back into our retrieval functions, send tool_result blocks back to the
model, terminate on stop_reason == "end_turn".

Layered on top of that:
  - Cumulative token cap and tool-call cap. Exceeding either triggers a
    final no-tools turn driven by EXHAUSTION_MESSAGE.
  - Citation block parse + corrective retry: if the first response is missing
    or malformed [[CITATIONS_BEGIN]] ... [[CITATIONS_END]], we send one
    corrective message with no tools. A second failure returns the answer
    with citations: [].
  - Tool errors are JSON-serialized into tool_result blocks with is_error=True
    so the model can recover.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...retrieval.wcs.convergence import Embedder
from ...retrieval.wcs.schemas import (
    NoteFilters,
    ToolError,
    TranscriptFilters,
)
from ...retrieval.wcs.tools import (
    get_note,
    get_transcript_window,
    search_notes,
    search_transcripts,
)
from .citations import (
    CitationParseError,
    EnrichedCitation,
    enrich_citations,
    parse_citations_block,
)
from .config import AgentConfig
from .prompts import (
    CORRECTIVE_RETRY_MESSAGE,
    EXHAUSTION_MESSAGE,
    SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result of one ``run_agent`` invocation.

    ``answer`` has the citation sentinel block stripped; ``citations`` is
    the enriched (DB-validated, visibility-filtered) list ready for the
    API response. ``dropped_citation_ids`` records IDs the model emitted
    that were rejected during enrichment (not in DB, not visible to
    viewer, malformed UUID). ``budget_exhausted`` and
    ``citation_parse_failed`` flag degraded paths so the response and
    eval logging can distinguish them from clean runs.
    ``tool_trace_id``, ``tool_calls_made``, and ``cumulative_tokens``
    are observability fields exposed for logging and eval pipelines.
    """

    answer: str
    citations: list[EnrichedCitation]
    dropped_citation_ids: list[str]
    budget_exhausted: bool
    citation_parse_failed: bool
    tool_trace_id: str
    tool_calls_made: int
    cumulative_tokens: int
    # Per-direction breakdown of ``cumulative_tokens``. Tracked separately
    # so the API can return a cost estimate (input and output are priced
    # very differently). ``cumulative_tokens`` remains the sum across all
    # four buckets for the budget check and existing log lines.
    cumulative_input_tokens: int
    cumulative_output_tokens: int
    # Prompt-cache buckets. ``cache_creation`` is billed at ~1.25x normal
    # input price; ``cache_read`` at ~0.10x. We track them separately so
    # cost can be computed accurately and caching effectiveness audited
    # (a high cache_read / low cache_creation ratio means the cache is
    # warm and saving money).
    cumulative_cache_creation_tokens: int
    cumulative_cache_read_tokens: int


# ── Tool definitions ──────────────────────────────────────────────────────────

_NOTE_FILTERS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "date_from": {"type": "string", "format": "date"},
        "date_to": {"type": "string", "format": "date"},
        "instructors": {"type": "array", "items": {"type": "string"}},
        "session_type": {"type": "string"},
        "organization": {"type": "string"},
    },
}

_TRANSCRIPT_FILTERS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "date_from": {"type": "string", "format": "date"},
        "date_to": {"type": "string", "format": "date"},
        "instructors": {"type": "array", "items": {"type": "string"}},
    },
}


def _tool_definitions() -> list[dict]:
    return [
        {
            "name": "search_notes",
            "description": (
                "Vector search over WCS lesson notes (flattened summary, key concepts, drills, etc). "
                "Returns up to k hits with title, session_date, instructors, students, organization, "
                "and a snippet. No source_url on hits — use get_note before citing."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "description": "Default 10, max 25."},
                    "filters": _NOTE_FILTERS_SCHEMA,
                },
                "required": ["query"],
            },
        },
        {
            "name": "search_transcripts",
            "description": (
                "Vector search over raw transcript chunks. Each hit has chunk_id "
                '("<transcript_uuid>:<chunk_index>"), linked note title and date, and a snippet. '
                "No source_url on hits — use get_transcript_window before citing."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer"},
                    "filters": _TRANSCRIPT_FILTERS_SCHEMA,
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_note",
            "description": "Fetch the full structured note by id. Returns notes_json and source_url.",
            "input_schema": {
                "type": "object",
                "properties": {"note_id": {"type": "string"}},
                "required": ["note_id"],
            },
        },
        {
            "name": "get_transcript_window",
            "description": (
                "Fetch a window of consecutive transcript chunks around the target chunk_id. "
                "before/after specify how many chunks on each side."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "before": {"type": "integer", "description": "Default 1."},
                    "after": {"type": "integer", "description": "Default 1."},
                },
                "required": ["chunk_id"],
            },
        },
    ]


# ── Tool dispatch ─────────────────────────────────────────────────────────────


async def _dispatch_tool(
    *,
    name: str,
    inputs: dict,
    session: AsyncSession,
    embedder: Embedder,
    viewer_id: str,
    owner_id: str,
    config: AgentConfig,
) -> dict:
    """Dispatch a tool call to the underlying retrieval function."""
    if name == "search_notes":
        filters = NoteFilters(**inputs["filters"]) if inputs.get("filters") else None
        hits = await search_notes(
            session=session,
            embedder=embedder,
            viewer_id=viewer_id,
            embedding_model=config.embedding_model,
            flattener_version=config.flattener_version,
            query=inputs["query"],
            k=int(inputs.get("k", 10)),
            filters=filters,
        )
        return {"hits": [h.model_dump(mode="json") for h in hits]}
    if name == "search_transcripts":
        filters = (
            TranscriptFilters(**inputs["filters"]) if inputs.get("filters") else None
        )
        hits = await search_transcripts(
            session=session,
            embedder=embedder,
            viewer_id=viewer_id,
            owner_id=owner_id,
            embedding_model=config.embedding_model,
            chunking_version=config.chunking_version,
            query=inputs["query"],
            k=int(inputs.get("k", 10)),
            filters=filters,
        )
        return {"hits": [h.model_dump(mode="json") for h in hits]}
    if name == "get_note":
        try:
            note_uuid = uuid.UUID(inputs["note_id"])
        except (ValueError, TypeError, KeyError) as e:
            raise ToolError(
                "invalid_input", f"Bad note_id: {inputs.get('note_id')!r}"
            ) from e
        note = await get_note(
            session=session,
            viewer_id=viewer_id,
            site_url=config.site_url,
            note_id=note_uuid,
        )
        return note.model_dump(mode="json")
    if name == "get_transcript_window":
        window = await get_transcript_window(
            session=session,
            owner_id=owner_id,
            site_url=config.site_url,
            embedding_model=config.embedding_model,
            chunking_version=config.chunking_version,
            chunk_id=inputs["chunk_id"],
            before=int(inputs.get("before", 1)),
            after=int(inputs.get("after", 1)),
        )
        return window.model_dump(mode="json")
    raise ToolError("invalid_input", f"Unknown tool: {name}")


# ── Block helpers (work with Anthropic SDK objects or plain dicts) ────────────


def _block_type(b: Any) -> str | None:
    if isinstance(b, dict):
        return b.get("type")
    return getattr(b, "type", None)


def _block_text(b: Any) -> str:
    if isinstance(b, dict):
        return b.get("text", "")
    return getattr(b, "text", "") or ""


def _block_id(b: Any) -> str:
    if isinstance(b, dict):
        return b.get("id", "")
    return getattr(b, "id", "") or ""


def _block_name(b: Any) -> str:
    if isinstance(b, dict):
        return b.get("name", "")
    return getattr(b, "name", "") or ""


def _block_input(b: Any) -> dict:
    if isinstance(b, dict):
        return b.get("input", {}) or {}
    return getattr(b, "input", {}) or {}


def _block_to_dict(b: Any) -> dict:
    if isinstance(b, dict):
        return b
    t = _block_type(b)
    if t == "text":
        return {"type": "text", "text": _block_text(b)}
    if t == "tool_use":
        return {
            "type": "tool_use",
            "id": _block_id(b),
            "name": _block_name(b),
            "input": _block_input(b),
        }
    return {"type": t}


@dataclass(frozen=True)
class _UsageBreakdown:
    """Per-call token accounting from an Anthropic LLM response.

    ``cache_creation_input_tokens`` are tokens written into the prompt
    cache on this call (billed at ~1.25x normal input). ``cache_read_input_tokens``
    are tokens served from a prior cache write within the cache TTL
    (billed at ~0.10x normal input). The four buckets together cover the
    whole bill for one call.
    """

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


def _read_usage_int(usage: Any, key: str) -> int:
    """Read a single int field from either dict-shaped or attribute-shaped usage."""
    if isinstance(usage, dict):
        return int(usage.get(key) or 0)
    return int(getattr(usage, key, 0) or 0)


def _usage_breakdown(response: Any) -> _UsageBreakdown:
    """Return a ``_UsageBreakdown`` from an LLM response's ``usage`` block.

    Tolerates dict-shaped and attribute-shaped usage objects (the Anthropic
    SDK returns the latter; some test stubs use the former). Missing fields
    default to 0 — the caller treats absence as "no usage reported" rather
    than failing the run. Cache fields are 0 when the response did not use
    prompt caching.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return _UsageBreakdown(0, 0, 0, 0)
    return _UsageBreakdown(
        input_tokens=_read_usage_int(usage, "input_tokens"),
        output_tokens=_read_usage_int(usage, "output_tokens"),
        cache_creation_input_tokens=_read_usage_int(usage, "cache_creation_input_tokens"),
        cache_read_input_tokens=_read_usage_int(usage, "cache_read_input_tokens"),
    )


def _usage_total(response: Any) -> int:
    """Legacy combined-total helper. Sum of every input bucket plus output."""
    u = _usage_breakdown(response)
    return (
        u.input_tokens
        + u.output_tokens
        + u.cache_creation_input_tokens
        + u.cache_read_input_tokens
    )


def _join_text(response: Any) -> str:
    return "".join(_block_text(b) for b in response.content if _block_type(b) == "text")


# ── Main loop ─────────────────────────────────────────────────────────────────


async def _call(
    *,
    client: Any,
    config: AgentConfig,
    messages: list[dict],
    tools: list[dict],
) -> Any:
    """Invoke the model with Anthropic prompt caching on system + tools.

    The cache breakpoint sits on the last tool, which tells Anthropic to
    cache everything before it (system prompt + every tool schema) as a
    single ephemeral prefix with a 5-minute TTL. Inside one agent run we
    make many LLM calls with identical system+tools, so each call after
    the first reads the cached prefix at ~10% of normal input price. Across
    asks, the cache also persists for 5 minutes, so back-to-back questions
    benefit too.

    Cache creation is a one-time premium (~1.25x input price) we pay on
    the first call; ``pricing.compute_cost_usd`` accounts for it.
    """
    cached_tools = list(tools)
    if cached_tools:
        cached_tools[-1] = {
            **cached_tools[-1],
            "cache_control": {"type": "ephemeral"},
        }
    return await client.messages.create(
        model=config.model,
        max_tokens=config.max_output_tokens,
        system=SYSTEM_PROMPT,
        tools=cached_tools,
        messages=messages,
    )


async def run_agent(
    *,
    question: str,
    session: AsyncSession,
    embedder: Embedder,
    anthropic_client: Any,
    config: AgentConfig,
    viewer_id: str,
    owner_id: str,
) -> AgentResult:
    """Run the WCS Q&A tool-use loop end-to-end."""
    tool_trace_id = uuid.uuid4().hex
    tool_definitions = _tool_definitions()

    messages: list[dict] = [{"role": "user", "content": question}]
    cumulative_tokens = 0
    cumulative_input_tokens = 0
    cumulative_output_tokens = 0
    cumulative_cache_creation_tokens = 0
    cumulative_cache_read_tokens = 0
    tool_calls_made = 0
    budget_exhausted = False
    final_text = ""

    while True:
        if cumulative_tokens >= config.max_input_tokens:
            logger.info(
                "wcs_qa.exhaustion: token cap reached trace=%s tokens=%d cap=%d",
                tool_trace_id,
                cumulative_tokens,
                config.max_input_tokens,
            )
            budget_exhausted = True
            break
        if tool_calls_made >= config.max_tool_calls:
            logger.info(
                "wcs_qa.exhaustion: tool cap reached trace=%s calls=%d cap=%d",
                tool_trace_id,
                tool_calls_made,
                config.max_tool_calls,
            )
            budget_exhausted = True
            break

        response = await _call(
            client=anthropic_client,
            config=config,
            messages=messages,
            tools=tool_definitions,
        )
        u = _usage_breakdown(response)
        cumulative_input_tokens += u.input_tokens
        cumulative_output_tokens += u.output_tokens
        cumulative_cache_creation_tokens += u.cache_creation_input_tokens
        cumulative_cache_read_tokens += u.cache_read_input_tokens
        cumulative_tokens += (
            u.input_tokens
            + u.output_tokens
            + u.cache_creation_input_tokens
            + u.cache_read_input_tokens
        )
        messages.append(
            {
                "role": "assistant",
                "content": [_block_to_dict(b) for b in response.content],
            }
        )

        if response.stop_reason == "end_turn":
            final_text = _join_text(response)
            break

        if response.stop_reason == "tool_use":
            tool_results: list[dict] = []
            for tu in response.content:
                if _block_type(tu) != "tool_use":
                    continue
                tool_use_id = _block_id(tu)
                tool_name = _block_name(tu)
                tool_input = _block_input(tu)
                tool_calls_made += 1
                if tool_calls_made > config.max_tool_calls:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(
                                {
                                    "error": "budget_exhausted",
                                    "message": "Tool call cap reached.",
                                }
                            ),
                            "is_error": True,
                        }
                    )
                    continue
                try:
                    result = await _dispatch_tool(
                        name=tool_name,
                        inputs=tool_input,
                        session=session,
                        embedder=embedder,
                        viewer_id=viewer_id,
                        owner_id=owner_id,
                        config=config,
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(result, default=str),
                        }
                    )
                except ToolError as e:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(
                                {"error": e.code, "message": e.message}
                            ),
                            "is_error": True,
                        }
                    )
                    logger.info(
                        "wcs_qa.tool_error trace=%s tool=%s code=%s",
                        tool_trace_id,
                        tool_name,
                        e.code,
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason — extract whatever text we have and end.
        final_text = _join_text(response)
        logger.warning(
            "wcs_qa.unexpected_stop_reason trace=%s reason=%s",
            tool_trace_id,
            response.stop_reason,
        )
        break

    if budget_exhausted:
        messages.append({"role": "user", "content": EXHAUSTION_MESSAGE})
        response = await _call(
            client=anthropic_client,
            config=config,
            messages=messages,
            tools=[],
        )
        u = _usage_breakdown(response)
        cumulative_input_tokens += u.input_tokens
        cumulative_output_tokens += u.output_tokens
        cumulative_cache_creation_tokens += u.cache_creation_input_tokens
        cumulative_cache_read_tokens += u.cache_read_input_tokens
        cumulative_tokens += (
            u.input_tokens
            + u.output_tokens
            + u.cache_creation_input_tokens
            + u.cache_read_input_tokens
        )
        messages.append(
            {
                "role": "assistant",
                "content": [_block_to_dict(b) for b in response.content],
            }
        )
        final_text = _join_text(response)

    parsed = parse_citations_block(final_text)
    if isinstance(parsed, CitationParseError):
        logger.info(
            "wcs_qa.citation_parse_failed trace=%s code=%s",
            tool_trace_id,
            parsed.code,
        )
        messages.append({"role": "user", "content": CORRECTIVE_RETRY_MESSAGE})
        retry_response = await _call(
            client=anthropic_client,
            config=config,
            messages=messages,
            tools=[],
        )
        u = _usage_breakdown(retry_response)
        cumulative_input_tokens += u.input_tokens
        cumulative_output_tokens += u.output_tokens
        cumulative_cache_creation_tokens += u.cache_creation_input_tokens
        cumulative_cache_read_tokens += u.cache_read_input_tokens
        cumulative_tokens += (
            u.input_tokens
            + u.output_tokens
            + u.cache_creation_input_tokens
            + u.cache_read_input_tokens
        )
        messages.append(
            {
                "role": "assistant",
                "content": [_block_to_dict(b) for b in retry_response.content],
            }
        )
        retry_text = _join_text(retry_response)
        retry_parsed = parse_citations_block(retry_text)
        if isinstance(retry_parsed, CitationParseError):
            logger.warning(
                "wcs_qa.citation_parse_failed_after_retry trace=%s code=%s",
                tool_trace_id,
                retry_parsed.code,
            )
            return AgentResult(
                answer=retry_text,
                citations=[],
                dropped_citation_ids=[],
                budget_exhausted=budget_exhausted,
                citation_parse_failed=True,
                tool_trace_id=tool_trace_id,
                tool_calls_made=tool_calls_made,
                cumulative_tokens=cumulative_tokens,
                cumulative_input_tokens=cumulative_input_tokens,
                cumulative_output_tokens=cumulative_output_tokens,
                cumulative_cache_creation_tokens=cumulative_cache_creation_tokens,
                cumulative_cache_read_tokens=cumulative_cache_read_tokens,
            )
        parsed = retry_parsed

    enriched, dropped = await enrich_citations(
        session=session,
        entries=parsed.raw_entries,
        viewer_id=viewer_id,
        owner_id=owner_id,
        site_url=config.site_url,
    )

    return AgentResult(
        answer=parsed.text_without_block,
        citations=enriched,
        dropped_citation_ids=dropped,
        budget_exhausted=budget_exhausted,
        citation_parse_failed=False,
        tool_trace_id=tool_trace_id,
        tool_calls_made=tool_calls_made,
        cumulative_tokens=cumulative_tokens,
        cumulative_input_tokens=cumulative_input_tokens,
        cumulative_output_tokens=cumulative_output_tokens,
        cumulative_cache_creation_tokens=cumulative_cache_creation_tokens,
        cumulative_cache_read_tokens=cumulative_cache_read_tokens,
    )
