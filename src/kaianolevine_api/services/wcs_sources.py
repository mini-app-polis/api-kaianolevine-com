"""WCS source ingest — create/update source + active extraction."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import WcsSource, WcsSourceExtraction, WcsTranscript
from ..schemas import WcsSourceCreate
from .wcs_composition import CompositionResult, compose_source


class SourceIngestError(Exception):
    """Domain error during source ingest (mapped to HTTP by the router)."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def ingest_source(
    session: AsyncSession,
    owner_id: str,
    payload: WcsSourceCreate,
) -> tuple[WcsSource, CompositionResult]:
    """Create or update a source, promote a new extraction, and compose."""
    transcript = await session.get(WcsTranscript, payload.transcript_id)
    if transcript is None:
        raise SourceIngestError(
            "transcript_not_found", "Transcript not found", status_code=404
        )
    if transcript.owner_id != owner_id:
        raise SourceIngestError(
            "transcript_not_owned",
            "Transcript not owned by this caller",
            status_code=403,
        )

    existing = await session.execute(
        select(WcsSource).where(WcsSource.transcript_id == payload.transcript_id)
    )
    source = existing.scalars().first()

    if source is None:
        source = WcsSource(
            owner_id=owner_id,
            transcript_id=payload.transcript_id,
        )
        session.add(source)
    elif source.owner_id != owner_id:
        raise SourceIngestError(
            "source_not_owned",
            "Existing source not owned by this caller",
            status_code=403,
        )

    source.title = payload.title
    source.session_date = payload.session_date
    source.session_type = payload.session_type
    source.instructors_raw = payload.instructors_raw
    source.students_raw = payload.students_raw
    source.organization = payload.organization
    source.visibility = payload.visibility
    source.is_default_visible = payload.is_default_visible

    await session.flush()

    await session.execute(
        update(WcsSourceExtraction)
        .where(
            WcsSourceExtraction.source_id == source.id,
            WcsSourceExtraction.is_active.is_(True),
        )
        .values(is_active=False)
    )

    raw_output = payload.raw_output.model_dump(by_alias=True)
    extraction = WcsSourceExtraction(
        source_id=source.id,
        extractor_version=payload.extractor_version,
        extractor_model=payload.extractor_model,
        extractor_provider=payload.extractor_provider,
        prompt_version=payload.prompt_version,
        raw_output=raw_output,
        is_active=True,
    )
    session.add(extraction)
    await session.flush()

    composition = await compose_source(session, source.id)
    return source, composition
