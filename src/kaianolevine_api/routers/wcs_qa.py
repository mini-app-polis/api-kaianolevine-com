"""WCS Q&A endpoints.

POST /v1/wcs/embeddings/refresh — admin-only. Runs the convergence flow that
embeds every note and chunks/embeds every transcript pending under the
current (model, flattener_version, chunking_version) configuration.

POST /v1/wcs/ask — authed users. Runs the WCS Q&A agent loop end-to-end
against the corpus and returns the citation-enriched answer.
"""

from __future__ import annotations

from typing import Literal

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends
from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_FAILURE, LOG_START, LOG_SUCCESS
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.wcs_qa.citations import EnrichedCitation
from ..agents.wcs_qa.config import from_settings as agent_config_from_settings
from ..agents.wcs_qa.loop import run_agent
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
    depth: Literal["normal", "deep"] = Field(
        default="normal",
        description=(
            "Per-request budget preset. ``normal`` (default) uses the "
            "configured ``WCS_QA_MAX_*_DEFAULT`` budgets and is appropriate "
            "for typical single-topic questions. ``deep`` raises the budgets "
            "for synthesis-heavy questions ('top N across all lessons', "
            "'summarize everything about X'); still clamped server-side to "
            "the ``WCS_QA_MAX_*_LIMIT`` ceilings."
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
    config = agent_config_from_settings(settings, depth=body.depth)
    log.info(
        "%s WCS Q&A ask owner=%s model=%s depth=%s q_len=%d budgets=tools:%d/input:%d/output:%d",
        LOG_START,
        owner_id,
        config.model,
        config.depth,
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
    log.info(
        "%s WCS Q&A ask trace=%s tokens=%d tool_calls=%d budget_exhausted=%s parse_failed=%s dropped=%d",
        LOG_SUCCESS,
        result.tool_trace_id,
        result.cumulative_tokens,
        result.tool_calls_made,
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
        ),
        count=1,
        total=1,
        version=settings.API_VERSION,
    )
