"""DB queries backing the WCS Q&A retrieval tools.

Two execution paths:
  - Postgres production: SQL-side filtering + pgvector cosine distance ordering.
  - SQLite tests:        SQL-side metadata filtering, Python-side vector ranking.

Filters are applied before vector ranking on Postgres so the existing btree/GIN
indexes do their job. On SQLite the candidate set is small enough that loading
all rows and ranking in Python is fine.

Visibility rules mirror services.wcs_access.user_can_see_note:
  default-visible OR caller is admin OR caller has a grant.
"""

from __future__ import annotations

import math
import uuid

from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    WcsNote,
    WcsNoteEmbedding,
    WcsNoteGrant,
    WcsTranscript,
    WcsTranscriptChunk,
    WcsUserProfile,
)
from .schemas import NoteFilters, TranscriptFilters


def _is_postgres(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bind.dialect.name == "postgresql"


async def _build_visibility_clause(session: AsyncSession, viewer_id: str):
    """Return a SQL clause restricting wcs_notes rows to those visible to viewer.

    Returns sqlalchemy true() if the caller is an admin (no restriction).
    """
    profile_q = await session.execute(
        select(WcsUserProfile).where(WcsUserProfile.user_id == viewer_id)
    )
    profile = profile_q.scalars().first()
    if profile is not None and profile.is_admin:
        return true()

    grant_subq = select(WcsNoteGrant.note_id).where(
        WcsNoteGrant.user_id == viewer_id
    )
    return or_(
        WcsNote.is_default_visible.is_(True),
        WcsNote.id.in_(grant_subq),
    )


def _apply_note_metadata_filters(stmt, filters: NoteFilters | None):
    if filters is None:
        return stmt
    if filters.date_from is not None:
        stmt = stmt.where(WcsNote.session_date >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(WcsNote.session_date <= filters.date_to)
    if filters.session_type is not None:
        stmt = stmt.where(WcsNote.session_type == filters.session_type)
    if filters.organization is not None:
        stmt = stmt.where(WcsNote.organization == filters.organization)
    return stmt


def _apply_transcript_metadata_filters(stmt, filters: TranscriptFilters | None):
    if filters is None:
        return stmt
    if filters.date_from is not None:
        stmt = stmt.where(WcsNote.session_date >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(WcsNote.session_date <= filters.date_to)
    return stmt


def _instructors_match(note_instructors: list[str], wanted: list[str] | None) -> bool:
    if not wanted:
        return True
    return bool(set(note_instructors or []) & set(wanted))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def search_notes_db(
    *,
    session: AsyncSession,
    query_vec: list[float],
    k: int,
    filters: NoteFilters | None,
    viewer_id: str,
    embedding_model: str,
    flattener_version: int,
) -> list[tuple[WcsNote, float]]:
    """Return up to k (note, score) pairs visible to viewer, ordered by similarity."""
    visibility = await _build_visibility_clause(session, viewer_id)

    if _is_postgres(session):
        distance = WcsNoteEmbedding.embedding.cosine_distance(query_vec)
        stmt = (
            select(WcsNote, distance.label("dist"))
            .join(WcsNoteEmbedding, WcsNoteEmbedding.note_id == WcsNote.id)
            .where(
                WcsNoteEmbedding.embedding_model == embedding_model,
                WcsNoteEmbedding.flattener_version == flattener_version,
                visibility,
            )
        )
        stmt = _apply_note_metadata_filters(stmt, filters)
        if filters and filters.instructors:
            stmt = stmt.where(WcsNote.instructors.overlap(filters.instructors))
        stmt = stmt.order_by(distance.asc()).limit(k)
        rows = (await session.execute(stmt)).all()
        return [(note, 1.0 - float(dist)) for note, dist in rows]

    # SQLite path: pull candidates, score in Python.
    stmt = (
        select(WcsNote, WcsNoteEmbedding.embedding)
        .join(WcsNoteEmbedding, WcsNoteEmbedding.note_id == WcsNote.id)
        .where(
            WcsNoteEmbedding.embedding_model == embedding_model,
            WcsNoteEmbedding.flattener_version == flattener_version,
            visibility,
        )
    )
    stmt = _apply_note_metadata_filters(stmt, filters)
    rows = (await session.execute(stmt)).all()
    wanted = filters.instructors if filters else None
    scored: list[tuple[WcsNote, float]] = []
    for note, embedding in rows:
        if not _instructors_match(list(note.instructors or []), wanted):
            continue
        scored.append((note, _cosine_similarity(query_vec, list(embedding))))
    scored.sort(key=lambda r: r[1], reverse=True)
    return scored[:k]


async def search_transcripts_db(
    *,
    session: AsyncSession,
    query_vec: list[float],
    k: int,
    filters: TranscriptFilters | None,
    viewer_id: str,  # noqa: ARG001 — transcripts have no grant model in v1
    owner_id: str,
    embedding_model: str,
    chunking_version: int,
) -> list[tuple[WcsTranscriptChunk, WcsTranscript, WcsNote | None, float]]:
    """Return up to k (chunk, transcript, linked_note_or_none, score) tuples.

    Owner-scoped (transcripts have no grant model in v1). Date and instructor
    filters are joined through the linked WcsNote when present.
    """
    if _is_postgres(session):
        distance = WcsTranscriptChunk.embedding.cosine_distance(query_vec)
        stmt = (
            select(WcsTranscriptChunk, WcsTranscript, WcsNote, distance.label("dist"))
            .join(
                WcsTranscript,
                WcsTranscript.id == WcsTranscriptChunk.transcript_id,
            )
            .outerjoin(WcsNote, WcsNote.transcript_id == WcsTranscript.id)
            .where(
                WcsTranscriptChunk.embedding_model == embedding_model,
                WcsTranscriptChunk.chunking_version == chunking_version,
                WcsTranscriptChunk.owner_id == owner_id,
            )
        )
        stmt = _apply_transcript_metadata_filters(stmt, filters)
        if filters and filters.instructors:
            stmt = stmt.where(WcsNote.instructors.overlap(filters.instructors))
        stmt = stmt.order_by(distance.asc()).limit(k)
        rows = (await session.execute(stmt)).all()
        return [
            (chunk, transcript, note, 1.0 - float(dist))
            for chunk, transcript, note, dist in rows
        ]

    # SQLite path
    stmt = (
        select(WcsTranscriptChunk, WcsTranscript, WcsNote)
        .join(
            WcsTranscript,
            WcsTranscript.id == WcsTranscriptChunk.transcript_id,
        )
        .outerjoin(WcsNote, WcsNote.transcript_id == WcsTranscript.id)
        .where(
            WcsTranscriptChunk.embedding_model == embedding_model,
            WcsTranscriptChunk.chunking_version == chunking_version,
            WcsTranscriptChunk.owner_id == owner_id,
        )
    )
    stmt = _apply_transcript_metadata_filters(stmt, filters)
    rows = (await session.execute(stmt)).all()

    wanted = filters.instructors if filters else None
    scored: list[tuple[WcsTranscriptChunk, WcsTranscript, WcsNote | None, float]] = []
    seen_chunk_ids: set[str] = set()
    for chunk, transcript, note in rows:
        if chunk.chunk_id in seen_chunk_ids:
            continue
        if not _instructors_match(
            list(note.instructors or []) if note is not None else [], wanted
        ):
            continue
        seen_chunk_ids.add(chunk.chunk_id)
        score = _cosine_similarity(query_vec, list(chunk.embedding))
        scored.append((chunk, transcript, note, score))
    scored.sort(key=lambda r: r[3], reverse=True)
    return scored[:k]


async def fetch_note(
    session: AsyncSession, note_id: uuid.UUID
) -> WcsNote | None:
    result = await session.execute(select(WcsNote).where(WcsNote.id == note_id))
    return result.scalars().first()


async def fetch_transcript_chunks(
    session: AsyncSession,
    transcript_id: uuid.UUID,
    *,
    embedding_model: str,
    chunking_version: int,
) -> list[WcsTranscriptChunk]:
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
    result = await session.execute(
        select(WcsTranscript).where(WcsTranscript.id == transcript_id)
    )
    return result.scalars().first()


async def fetch_linked_note_for_transcript(
    session: AsyncSession, transcript_id: uuid.UUID
) -> WcsNote | None:
    """Returns the single linked note when there's exactly one, else None."""
    result = await session.execute(
        select(WcsNote).where(WcsNote.transcript_id == transcript_id)
    )
    notes = list(result.scalars().all())
    return notes[0] if len(notes) == 1 else None
