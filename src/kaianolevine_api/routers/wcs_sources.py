"""WCS sources router — POST /v1/wcs/sources.

Called by transcription-cog after extracting a transcript. Creates the
wcs_sources row, writes the new active wcs_source_extractions row, and
runs the Composition Service synchronously to derive canonical-layer
rows before returning.

# Synchronous composition (deliberate)

Composition runs in the request lifecycle — the endpoint does not return
until canonical-layer rows are written. This is a proof-of-concept-scale
choice. Reasons:

  - Simplicity: callers (transcription-cog) get a deterministic
    "canonical layer is current" guarantee on 200 response. No follow-up
    polling, no eventual-consistency caveats in downstream readers.
  - Atomicity: source + extraction + canonical rows commit in one
    transaction. Composition failure rolls the whole write back, so the
    DB never holds an extraction without its derived canonical rows.
  - Corpus size: ~100 sources at scale, each composing in well under a
    second. The synchronous cost is invisible at this scale.

# When to revisit

Move composition to a background task (Prefect flow, FastAPI BackgroundTask,
or a deferred queue) if any of these become true:

  - Composition for a single source exceeds ~2 seconds, making the POST
    feel slow to transcription-cog's flow.
  - Bulk re-composition (e.g. after a global name correction) needs to
    fan out across many sources; doing that synchronously inside a single
    request blocks the worker.
  - The endpoint moves to a context where the caller can't tolerate
    request-level coupling to composition (e.g. user-facing UI ingestion).

Until one of those triggers, synchronous composition is the right call.
The cost of moving to async later is small: change compose_source's
invocation from ``await compose_source(...)`` to a task enqueue, and add
a status field on wcs_source_extractions to track composition state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_FAILURE, LOG_START, LOG_SUCCESS
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_owner
from ..config import get_settings
from ..database import get_db_session
from ..schemas import (
    Envelope,
    WcsSourceCreate,
    WcsSourceItem,
    api_error,
    success_envelope,
)
from ..services.wcs_sources import SourceIngestError, ingest_source
from ..services.wcs_wiki import _source_item

router = APIRouter()
log = logger_mod.get_logger()


@router.post(
    "/wcs/sources",
    response_model=Envelope[WcsSourceItem],
    summary="Ingest a WCS source (lesson) with its extraction",
    description=(
        "Called by transcription-cog after extracting a transcript. "
        "Creates the wcs_sources row, writes the new extraction as the "
        "active wcs_source_extractions row, demotes any previous active "
        "extraction, then runs the Composition Service **synchronously** "
        "to derive canonical-layer rows. The endpoint returns once "
        "canonical rows are committed — there is no eventual consistency "
        "for downstream readers. See module docstring for the rationale "
        "and conditions under which this should be moved to a background "
        "task."
    ),
)
async def create_source(
    payload: WcsSourceCreate,
    owner_id: str = Depends(get_current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsSourceItem]:
    """Ingest one source and its active extraction."""
    log.info(
        "%s ingest source transcript_id=%s",
        LOG_START,
        payload.transcript_id,
    )
    settings = get_settings()

    try:
        source, composition = await ingest_source(session, owner_id, payload)
        await session.commit()
        await session.refresh(source)
    except SourceIngestError as exc:
        await session.rollback()
        raise api_error(exc.status_code, exc.code, exc.message) from exc
    except Exception as exc:
        await session.rollback()
        log.exception("%s composition failed: %s", LOG_FAILURE, exc)
        raise api_error(
            500,
            "composition_failed",
            f"Composition failed: {exc}",
        ) from exc

    log.info(
        "%s source ingested id=%s attributions=%d",
        LOG_SUCCESS,
        source.id,
        composition.attributions_written,
    )
    data = _source_item(source)
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)
