"""WCS Q&A endpoints.

POST /v1/wcs/embeddings/refresh — admin-only. Runs the convergence flow that
embeds every note and chunks/embeds every transcript pending under the
current (model, flattener_version, chunking_version) configuration.

POST /v1/wcs/ask — authed users. Runs the WCS Q&A agent loop end-to-end
against the corpus and returns the citation-enriched answer.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends
from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_FAILURE, LOG_START, LOG_SUCCESS
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.wcs_qa.citations import EnrichedCitation
from ..agents.wcs_qa.config import from_settings as agent_config_from_settings
from ..agents.wcs_qa.loop import run_agent
from ..agents.wcs_qa.pricing import compute_cost_usd
from ..auth import get_current_owner, require_wcs_admin
from ..config import Settings, get_settings
from ..database import get_db_session
from ..retrieval.wcs.convergence import RefreshSummary, refresh_embeddings
from ..retrieval.wcs.embed import OpenAIEmbedder
from ..schemas import Envelope, api_error, success_envelope

router = APIRouter()
log = logger_mod.get_logger()


def get_embedder(settings: Settings = Depends(get_settings)) -> OpenAIEmbedder:
    """Build the OpenAI embedder for the request lifecycle."""
    if not settings.OPENAI_API_KEY:
        raise api_error(
            503, "embedding_unavailable", "OPENAI_API_KEY is not configured"
        )
    return OpenAIEmbedder(
        api_key=settings.OPENAI_API_KEY,
        model=settings.WCS_QA_EMBEDDING_MODEL,
    )


def get_anthropic_client(
    settings: Settings = Depends(get_settings),
) -> AsyncAnthropic:
    """Build the Anthropic client for the request lifecycle."""
    if not settings.ANTHROPIC_API_KEY:
        raise api_error(503, "agent_unavailable", "ANTHROPIC_API_KEY is not configured")
    return AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


class AskRequest(BaseModel):
    """Request body for ``POST /v1/wcs/ask``.

    Single-turn Q&A — every request is a fresh agent run; there is no
    conversation history field in v1.
    """

    question: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description=(
            "The user's natural-language question for the WCS Q&A agent. "
            "Required, 1–5000 characters."
        ),
    )


class AskUsage(BaseModel):
    """Token + estimated cost summary for one ``/wcs/ask`` run.

    ``cost_usd`` is computed server-side from per-model pricing (see
    ``agents/wcs_qa/pricing.py``). It is ``None`` when the model isn't in
    the pricing table — the client should render "cost unknown" in that
    case rather than $0. The number is an *estimate* and excludes the
    OpenAI embedding cost on the question (negligible).
    """

    model: str = Field(
        ...,
        description="Anthropic model id the agent used (e.g. ``claude-sonnet-4-6``).",
    )
    input_tokens: int = Field(
        ...,
        ge=0,
        description=(
            "Cumulative *fresh* input tokens across every LLM call made by "
            "the agent loop for this run. Does not include tokens served "
            "from prompt cache (see ``cache_read_tokens``)."
        ),
    )
    output_tokens: int = Field(
        ...,
        ge=0,
        description=(
            "Cumulative output tokens across every LLM call made by the "
            "agent loop for this run."
        ),
    )
    cache_creation_tokens: int = Field(
        0,
        ge=0,
        description=(
            "Tokens written into the Anthropic prompt cache on this run "
            "(billed at ~1.25x normal input price). Typically only the "
            "first LLM call within a 5-minute cache window incurs creation."
        ),
    )
    cache_read_tokens: int = Field(
        0,
        ge=0,
        description=(
            "Tokens served from the Anthropic prompt cache on this run "
            "(billed at ~0.10x normal input price). High values relative "
            "to ``input_tokens`` indicate a warm cache — the system prompt "
            "and tool schemas are being reused across LLM calls."
        ),
    )
    cost_usd: float | None = Field(
        None,
        description=(
            "Estimated dollar cost of the LLM calls for this run, or "
            "``None`` if the model isn't in the server's pricing table. "
            "Reflects all four token buckets (input, output, cache write, "
            "cache read) at their respective prices."
        ),
    )


class AskResponse(BaseModel):
    """Response body for ``POST /v1/wcs/ask``.

    ``answer`` is the model's text reply with the internal citation
    sentinel block stripped — inline ``[N]`` markers remain so the
    client can pair them with entries in ``citations``.
    """

    answer: str = Field(
        ...,
        description=(
            "The agent's natural-language answer, with the internal citation "
            "sentinel block removed. Inline ``[N]`` markers remain so they "
            "can be paired with entries in ``citations``."
        ),
    )
    citations: list[EnrichedCitation] = Field(
        ...,
        description=(
            "Enriched citation list, one entry per ``[N]`` marker the model "
            "emitted that survived DB validation and visibility filtering. "
            "May be empty if the model returned no citations or both parse "
            "attempts failed."
        ),
    )
    budget_exhausted: bool = Field(
        ...,
        description=(
            "True if the agent loop was cut short by hitting the token cap "
            "or tool-call cap. The answer was produced by a final no-tools "
            "turn driven by an exhaustion message."
        ),
    )
    tool_trace_id: str = Field(
        ...,
        description=(
            "Hex-encoded UUID identifying this agent run in logs and the "
            "WCS Q&A eval pipeline. Surfacing it in the response lets users "
            "reference specific runs when reporting issues."
        ),
    )
    usage: AskUsage = Field(
        ...,
        description=(
            "Token usage and estimated cost for this run. Useful for "
            "displaying cost per ask in the UI and for ad-hoc audits."
        ),
    )


@router.post(
    "/wcs/embeddings/refresh",
    response_model=Envelope[RefreshSummary],
    summary="Refresh WCS embeddings",
    description=(
        "Admin-only. Embeds notes and transcript chunks pending under the "
        "current (embedding_model, flattener_version, chunking_version) config. "
        "Idempotent — re-running embeds nothing if no source content has changed."
    ),
)
async def refresh_wcs_embeddings(
    _admin_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    embedder: OpenAIEmbedder = Depends(get_embedder),
) -> Envelope[RefreshSummary]:
    """Run the convergence flow synchronously and return counts."""
    log.info(
        "%s WCS embedding refresh model=%s flat_v=%d chunk_v=%d",
        LOG_START,
        settings.WCS_QA_EMBEDDING_MODEL,
        settings.WCS_QA_FLATTENER_VERSION,
        settings.WCS_QA_CHUNKING_VERSION,
    )
    summary = await refresh_embeddings(
        session=session,
        embedder=embedder,
        embedding_model=settings.WCS_QA_EMBEDDING_MODEL,
        flattener_version=settings.WCS_QA_FLATTENER_VERSION,
        chunking_version=settings.WCS_QA_CHUNKING_VERSION,
    )
    log.info(
        "%s WCS embedding refresh notes=%d/%d chunks=%d duration_ms=%d",
        LOG_SUCCESS,
        summary.notes_embedded,
        summary.notes_total,
        summary.chunks_embedded,
        summary.duration_ms,
    )
    return success_envelope(summary, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/wcs/ask",
    response_model=Envelope[AskResponse],
    response_model_exclude_none=True,
    summary="WCS Q&A — single-turn ask",
    description=(
        "Authed users. Runs the WCS Q&A agent loop with the four retrieval tools, "
        "returns the citation-enriched answer. Visibility uses the same rules as "
        "the rest of the WCS site: default-visible OR admin OR explicit grant for "
        "notes; owner-scoped for transcripts."
    ),
)
async def ask(
    body: AskRequest,
    owner_id: str = Depends(get_current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    embedder: OpenAIEmbedder = Depends(get_embedder),
    anthropic_client: AsyncAnthropic = Depends(get_anthropic_client),
) -> Envelope[AskResponse]:
    """Run the WCS Q&A agent loop and return its citation-enriched answer."""
    config = agent_config_from_settings(settings)
    log.info(
        "%s WCS Q&A ask owner=%s model=%s q_len=%d budgets=tools:%d/input:%d/output:%d",
        LOG_START,
        owner_id,
        config.model,
        len(body.question),
        config.max_tool_calls,
        config.max_input_tokens,
        config.max_output_tokens,
    )
    try:
        result = await run_agent(
            question=body.question,
            session=session,
            embedder=embedder,
            anthropic_client=anthropic_client,
            config=config,
            viewer_id=owner_id,
            owner_id=owner_id,
        )
    except Exception:
        log.exception(
            "%s WCS Q&A ask failed owner=%s",
            LOG_FAILURE,
            owner_id,
        )
        raise
    cost_usd = compute_cost_usd(
        config.model,
        result.cumulative_input_tokens,
        result.cumulative_output_tokens,
        cache_creation_tokens=result.cumulative_cache_creation_tokens,
        cache_read_tokens=result.cumulative_cache_read_tokens,
    )
    log.info(
        "%s WCS Q&A ask trace=%s tokens=%d (in=%d/out=%d cache_w=%d cache_r=%d) tool_calls=%d cost_usd=%s budget_exhausted=%s parse_failed=%s dropped=%d",
        LOG_SUCCESS,
        result.tool_trace_id,
        result.cumulative_tokens,
        result.cumulative_input_tokens,
        result.cumulative_output_tokens,
        result.cumulative_cache_creation_tokens,
        result.cumulative_cache_read_tokens,
        result.tool_calls_made,
        f"{cost_usd:.4f}" if cost_usd is not None else "unknown",
        result.budget_exhausted,
        result.citation_parse_failed,
        len(result.dropped_citation_ids),
    )
    return success_envelope(
        AskResponse(
            answer=result.answer,
            citations=result.citations,
            budget_exhausted=result.budget_exhausted,
            tool_trace_id=result.tool_trace_id,
            usage=AskUsage(
                model=config.model,
                input_tokens=result.cumulative_input_tokens,
                output_tokens=result.cumulative_output_tokens,
                cache_creation_tokens=result.cumulative_cache_creation_tokens,
                cache_read_tokens=result.cumulative_cache_read_tokens,
                cost_usd=cost_usd,
            ),
        ),
        count=1,
        total=1,
        version=settings.API_VERSION,
    )
