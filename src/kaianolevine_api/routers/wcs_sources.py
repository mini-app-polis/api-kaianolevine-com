"""WCS sources router — ingest endpoint for transcription-cog."""

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
        "Creates the wcs_sources row (or updates the existing row for the "
        "same transcript_id), writes a new active wcs_source_extractions "
        "row (demoting any previous active extraction), then runs the "
        "Composition Service to derive canonical layer rows. Re-running "
        "against the same transcript produces a new extraction version on "
        "the existing source, not a duplicate source row."
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
