"""DB queries backing the WCS Q&A retrieval tools.

SQL-side filtering, then pgvector cosine distance ordering. Filters are
applied before vector ranking so the existing btree/GIN indexes do their job.

A lesson is a ``WcsSource``: its transcript's chunks are what the tools
search and read, and the source decides who may see them, exactly as the
wiki decides it (:func:`services.wcs_source_visibility.visible_sources_clause`).
The chunks' ``owner_id`` is still written, but it never decides visibility:
every chunk is owned by the transcription cog's machine principal, so an
owner filter matched no person.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import WcsSource, WcsTranscript, WcsTranscriptChunk
from ...services.wcs_source_visibility import visible_sources_clause
from .schemas import TranscriptFilters


def _apply_transcript_metadata_filters(stmt, filters: TranscriptFilters | None):
    if filters is None:
        return stmt
    if filters.date_from is not None:
        stmt = stmt.where(WcsSource.session_date >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(WcsSource.session_date <= filters.date_to)
    if filters.instructors:
        stmt = stmt.where(WcsSource.instructors_raw.overlap(filters.instructors))
    return stmt


async def search_transcripts_db(
    *,
    session: AsyncSession,
    query_vec: list[float],
    k: int,
    filters: TranscriptFilters | None,
    viewer_id: str,
    embedding_model: str,
    chunking_version: int,
) -> list[tuple[WcsTranscriptChunk, WcsTranscript, WcsSource, float]]:
    """Return up to k (chunk, transcript, source, score) tuples.

    Only chunks of lessons the viewer may see. Date and instructor filters
    read the lesson (the source).
    """
    visibility = await visible_sources_clause(session, viewer_id)
    distance = WcsTranscriptChunk.embedding.cosine_distance(query_vec)
    stmt = (
        select(WcsTranscriptChunk, WcsTranscript, WcsSource, distance.label("dist"))
        .join(WcsTranscript, WcsTranscript.id == WcsTranscriptChunk.transcript_id)
        .join(WcsSource, WcsSource.transcript_id == WcsTranscript.id)
        .where(
            WcsTranscriptChunk.embedding_model == embedding_model,
            WcsTranscriptChunk.chunking_version == chunking_version,
            visibility,
        )
    )
    stmt = _apply_transcript_metadata_filters(stmt, filters)
    stmt = stmt.order_by(distance.asc()).limit(k)
    rows = (await session.execute(stmt)).all()
    # One source per transcript is the rule (one canonical source per Drive
    # file); should a transcript ever carry two, its chunk is listed once.
    seen: set[str] = set()
    out: list[tuple[WcsTranscriptChunk, WcsTranscript, WcsSource, float]] = []
    for chunk, transcript, source, dist in rows:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        out.append((chunk, transcript, source, 1.0 - float(dist)))
    return out


async def fetch_transcript_chunks(
    session: AsyncSession,
    transcript_id: uuid.UUID,
    *,
    embedding_model: str,
    chunking_version: int,
) -> list[WcsTranscriptChunk]:
    """Return all chunks of a transcript at the given embedding/chunking config.

    Filtered to the requested ``(embedding_model, chunking_version)`` pair
    and ordered by ``chunk_index`` ascending so callers can assemble
    contiguous reading windows without re-sorting.
    """
    result = await session.execute(
        select(WcsTranscriptChunk)
        .where(
            WcsTranscriptChunk.transcript_id == transcript_id,
            WcsTranscriptChunk.embedding_model == embedding_model,
            WcsTranscriptChunk.chunking_version == chunking_version,
        )
        .order_by(WcsTranscriptChunk.chunk_index.asc())
    )
    return list(result.scalars().all())


async def fetch_transcript(
    session: AsyncSession, transcript_id: uuid.UUID
) -> WcsTranscript | None:
    """Return a single ``WcsTranscript`` by primary key, or None if not found.

    No visibility check: callers check the transcript's lesson with
    :func:`fetch_visible_source_for_transcript`.
    """
    result = await session.execute(
        select(WcsTranscript).where(WcsTranscript.id == transcript_id)
    )
    return result.scalars().first()


async def fetch_visible_source_for_transcript(
    session: AsyncSession, transcript_id: uuid.UUID, viewer_id: str
) -> WcsSource | None:
    """The transcript's lesson, if the viewer may see it; else None.

    None also when the transcript has no lesson. Callers answer both as
    "not found", so a hidden lesson's existence does not leak.
    """
    visibility = await visible_sources_clause(session, viewer_id)
    result = await session.execute(
        select(WcsSource)
        .where(WcsSource.transcript_id == transcript_id, visibility)
        .order_by(WcsSource.id)
        .limit(1)
    )
    return result.scalars().first()


async def fetch_visible_source(
    session: AsyncSession, source_id: uuid.UUID, viewer_id: str
) -> WcsSource | None:
    """A lesson by id, if the viewer may see it; else None."""
    visibility = await visible_sources_clause(session, viewer_id)
    result = await session.execute(
        select(WcsSource).where(WcsSource.id == source_id, visibility)
    )
    return result.scalars().first()
