"""Convergence flow for WCS Q&A embeddings.

Treats the embedding tables as a cache of the source corpus under a given
(embedding_model, flattener_version, chunking_version) configuration. On every
run, computes the canonical text for each source row, hashes it, and embeds
only rows whose stored hash doesn't match. No backfill step — the first run
embeds everything; subsequent runs no-op for unchanged rows.

For transcripts, content_sha is computed at the transcript level
(sha256(title + raw_text)) and stored on every chunk. A transcript is "fresh"
iff at least one of its chunks at the current config carries the matching SHA;
otherwise we delete its existing chunks and re-chunk + re-embed from scratch.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    WcsNote,
    WcsNoteEmbedding,
    WcsTranscript,
    WcsTranscriptChunk,
)
from .chunker import chunk_transcript
from .flatten import flatten_note


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class RefreshSummary(BaseModel):
    notes_total: int
    notes_embedded: int
    notes_skipped: int
    transcripts_total: int
    transcripts_embedded: int
    transcripts_skipped: int
    chunks_embedded: int
    duration_ms: int


async def refresh_embeddings(
    *,
    session: AsyncSession,
    embedder: Embedder,
    embedding_model: str,
    flattener_version: int,
    chunking_version: int,
) -> RefreshSummary:
    started = dt.datetime.now(dt.UTC)
    notes_stats = await _converge_notes(
        session=session,
        embedder=embedder,
        embedding_model=embedding_model,
        flattener_version=flattener_version,
    )
    transcripts_stats = await _converge_transcripts(
        session=session,
        embedder=embedder,
        embedding_model=embedding_model,
        chunking_version=chunking_version,
    )
    duration_ms = int((dt.datetime.now(dt.UTC) - started).total_seconds() * 1000)
    return RefreshSummary(
        notes_total=notes_stats["total"],
        notes_embedded=notes_stats["embedded"],
        notes_skipped=notes_stats["skipped"],
        transcripts_total=transcripts_stats["total"],
        transcripts_embedded=transcripts_stats["embedded"],
        transcripts_skipped=transcripts_stats["skipped"],
        chunks_embedded=transcripts_stats["chunks_embedded"],
        duration_ms=duration_ms,
    )


async def _converge_notes(
    *,
    session: AsyncSession,
    embedder: Embedder,
    embedding_model: str,
    flattener_version: int,
) -> dict[str, int]:
    notes_q = await session.execute(select(WcsNote))
    notes = list(notes_q.scalars().all())
    if not notes:
        return {"total": 0, "embedded": 0, "skipped": 0}

    flats: dict[uuid.UUID, str] = {}
    shas: dict[uuid.UUID, str] = {}
    for n in notes:
        flat = flatten_note(
            title=n.title,
            instructors=list(n.instructors or []),
            students=list(n.students or []),
            organization=n.organization,
            session_date=n.session_date,
            notes_json=n.notes_json or {},
        )
        flats[n.id] = flat
        shas[n.id] = hashlib.sha256(flat.encode("utf-8")).hexdigest()

    existing_q = await session.execute(
        select(WcsNoteEmbedding.note_id, WcsNoteEmbedding.content_sha).where(
            WcsNoteEmbedding.embedding_model == embedding_model,
            WcsNoteEmbedding.flattener_version == flattener_version,
        )
    )
    existing: dict[uuid.UUID, str] = {row[0]: row[1] for row in existing_q.all()}

    pending = [n for n in notes if existing.get(n.id) != shas[n.id]]
    if not pending:
        return {"total": len(notes), "embedded": 0, "skipped": len(notes)}

    pending_texts = [flats[n.id] for n in pending]
    vectors = await embedder.embed(pending_texts)
    if len(vectors) != len(pending):
        raise RuntimeError(
            f"Embedder returned {len(vectors)} vectors for {len(pending)} notes"
        )

    pending_ids = [n.id for n in pending]
    await session.execute(
        delete(WcsNoteEmbedding).where(
            WcsNoteEmbedding.note_id.in_(pending_ids),
            WcsNoteEmbedding.embedding_model == embedding_model,
            WcsNoteEmbedding.flattener_version == flattener_version,
        )
    )
    for n, vec in zip(pending, vectors, strict=True):
        session.add(
            WcsNoteEmbedding(
                note_id=n.id,
                owner_id=n.owner_id,
                embedding=vec,
                embedding_model=embedding_model,
                flattener_version=flattener_version,
                content_sha=shas[n.id],
            )
        )
    await session.commit()

    return {
        "total": len(notes),
        "embedded": len(pending),
        "skipped": len(notes) - len(pending),
    }


async def _converge_transcripts(
    *,
    session: AsyncSession,
    embedder: Embedder,
    embedding_model: str,
    chunking_version: int,
) -> dict[str, int]:
    transcripts_q = await session.execute(select(WcsTranscript))
    transcripts = list(transcripts_q.scalars().all())
    if not transcripts:
        return {"total": 0, "embedded": 0, "skipped": 0, "chunks_embedded": 0}

    titles: dict[uuid.UUID, str] = {}
    shas: dict[uuid.UUID, str] = {}
    for t in transcripts:
        title = await _resolve_transcript_title(session, t)
        titles[t.id] = title
        digest_input = f"{title}\n\n{t.raw_text}"
        shas[t.id] = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    existing_q = await session.execute(
        select(
            WcsTranscriptChunk.transcript_id, WcsTranscriptChunk.content_sha
        )
        .where(
            WcsTranscriptChunk.embedding_model == embedding_model,
            WcsTranscriptChunk.chunking_version == chunking_version,
        )
        .distinct()
    )
    existing_per_transcript: dict[uuid.UUID, str] = {}
    for row in existing_q.all():
        existing_per_transcript[row[0]] = row[1]

    pending = [t for t in transcripts if existing_per_transcript.get(t.id) != shas[t.id]]
    if not pending:
        return {
            "total": len(transcripts),
            "embedded": 0,
            "skipped": len(transcripts),
            "chunks_embedded": 0,
        }

    chunks_total = 0
    for t in pending:
        title = titles[t.id]
        chunks = chunk_transcript(t.raw_text)

        await session.execute(
            delete(WcsTranscriptChunk).where(
                WcsTranscriptChunk.transcript_id == t.id,
                WcsTranscriptChunk.embedding_model == embedding_model,
                WcsTranscriptChunk.chunking_version == chunking_version,
            )
        )
        if not chunks:
            await session.commit()
            continue

        embedding_inputs = [f"{title}\n\n{c.text}" for c in chunks]
        vectors = await embedder.embed(embedding_inputs)
        if len(vectors) != len(chunks):
            raise RuntimeError(
                f"Embedder returned {len(vectors)} vectors for {len(chunks)} chunks "
                f"of transcript {t.id}"
            )

        for c, vec in zip(chunks, vectors, strict=True):
            session.add(
                WcsTranscriptChunk(
                    chunk_id=f"{t.id}:{c.chunk_index}",
                    transcript_id=t.id,
                    owner_id=t.owner_id,
                    chunk_index=c.chunk_index,
                    start_offset=c.start_offset,
                    end_offset=c.end_offset,
                    text=c.text,
                    embedding=vec,
                    embedding_model=embedding_model,
                    chunking_version=chunking_version,
                    content_sha=shas[t.id],
                )
            )
        chunks_total += len(chunks)
        await session.commit()

    return {
        "total": len(transcripts),
        "embedded": len(pending),
        "skipped": len(transcripts) - len(pending),
        "chunks_embedded": chunks_total,
    }


async def _resolve_transcript_title(
    session: AsyncSession, transcript: WcsTranscript
) -> str:
    """Title for embedding composition.

    Uses the linked note's title when the transcript has exactly one note
    with a non-empty title; falls back to source_filename otherwise. This
    gives more semantic retrieval grounding when a note exists, while
    staying deterministic for transcripts with zero or multiple notes.
    """
    notes_q = await session.execute(
        select(WcsNote.title).where(WcsNote.transcript_id == transcript.id)
    )
    titles = [row[0] for row in notes_q.all()]
    if len(titles) == 1 and titles[0]:
        return titles[0]
    return transcript.source_filename
