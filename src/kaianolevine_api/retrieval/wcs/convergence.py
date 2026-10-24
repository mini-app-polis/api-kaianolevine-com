"""Convergence flow for WCS Q&A embeddings.

Treats the embedding tables as a cache of the source corpus under a given
(embedding_model, chunking_version, page_flattener_version) configuration. On every
run, computes the canonical text for each source row, hashes it, and embeds
only rows whose stored hash doesn't match. No backfill step — the first run
embeds everything; subsequent runs no-op for unchanged rows.

Wiki pages are keyed on (embedding_model, page_flattener_version)
and hashed over the page text the Q&A searches (``wiki_pages.flatten_page``).

For transcripts, content_sha is computed at the transcript level
(sha256(title + raw_text)) and stored on every chunk. The title is the
lesson's (its ``WcsSource``), or the transcript's filename when it has none. A transcript is "fresh"
iff at least one of its chunks at the current config carries the matching SHA;
otherwise we delete its existing chunks and re-chunk + re-embed from scratch.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Protocol

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    WcsSource,
    WcsTranscript,
    WcsTranscriptChunk,
    WcsWikiPageEmbedding,
    WcsWikiPageRow,
)
from .chunker import chunk_transcript


class Embedder(Protocol):
    """Async vector-embedder protocol used by the convergence flow.

    Implementations take a list of texts and return one embedding vector
    per text in the same order. ``OpenAIEmbedder`` in ``embed.py`` is the
    production implementation; tests pass in a stub that returns
    deterministic vectors.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in input order."""
        ...


class RefreshSummary(BaseModel):
    """Counts and timing returned by ``refresh_embeddings``.

    All counters are scoped to the current ``(embedding_model,
    chunking_version, page_flattener_version)`` configuration. ``embedded``
    counts how many source rows had their embeddings recomputed;
    ``skipped`` counts rows whose stored content_sha already matched.
    """

    transcripts_total: int = Field(
        ...,
        description=(
            "Total number of WCS transcripts considered for refresh under "
            "the current ``(embedding_model, chunking_version)`` config."
        ),
    )
    transcripts_embedded: int = Field(
        ...,
        description=(
            "Transcripts that were re-chunked and re-embedded during this run."
        ),
    )
    transcripts_skipped: int = Field(
        ...,
        description=(
            "Transcripts left untouched because at least one chunk already "
            "carried the matching content_sha."
        ),
    )
    chunks_embedded: int = Field(
        ...,
        description=(
            "Total number of transcript chunks newly embedded across all "
            "refreshed transcripts."
        ),
    )
    pages_total: int = Field(
        ...,
        description=(
            "Wiki pages considered under the current ``(embedding_model, "
            "page_flattener_version)`` config."
        ),
    )
    pages_embedded: int = Field(
        ..., description="Wiki pages whose embeddings were recomputed."
    )
    pages_skipped: int = Field(
        ...,
        description="Wiki pages left untouched because their text had not changed.",
    )
    duration_ms: int = Field(
        ...,
        description=(
            "Wall-clock time, in milliseconds, that the refresh took end to "
            "end (transcripts + wiki pages)."
        ),
    )


async def refresh_embeddings(
    *,
    session: AsyncSession,
    embedder: Embedder,
    embedding_model: str,
    chunking_version: int,
    page_flattener_version: int = 1,
) -> RefreshSummary:
    """Embed every transcript chunk and wiki page pending under the config.

    Treats the embedding tables as a content-addressed cache keyed on
    ``(embedding_model, chunking_version, page_flattener_version)`` plus the
    source row's flattened content. Rows whose stored ``content_sha``
    already matches are skipped; rows whose hash differs (or are missing)
    are re-embedded. Idempotent — re-running on unchanged content is a
    no-op. Returns a ``RefreshSummary`` with counts and timing.
    """
    started = dt.datetime.now(dt.UTC)
    transcripts_stats = await _converge_transcripts(
        session=session,
        embedder=embedder,
        embedding_model=embedding_model,
        chunking_version=chunking_version,
    )
    pages_stats = await _converge_pages(
        session=session,
        embedder=embedder,
        embedding_model=embedding_model,
        page_flattener_version=page_flattener_version,
    )
    duration_ms = int((dt.datetime.now(dt.UTC) - started).total_seconds() * 1000)
    return RefreshSummary(
        transcripts_total=transcripts_stats["total"],
        transcripts_embedded=transcripts_stats["embedded"],
        transcripts_skipped=transcripts_stats["skipped"],
        chunks_embedded=transcripts_stats["chunks_embedded"],
        pages_total=pages_stats["total"],
        pages_embedded=pages_stats["embedded"],
        pages_skipped=pages_stats["skipped"],
        duration_ms=duration_ms,
    )


async def _converge_pages(
    *,
    session: AsyncSession,
    embedder: Embedder,
    embedding_model: str,
    page_flattener_version: int,
) -> dict[str, int]:
    """Embed the wiki pages whose text changed.

    Reads only the parts of each document that are embedded, not the whole
    page. A removed page's rows go with it (ON DELETE CASCADE).
    """
    from .wiki_pages import flatten_page

    rows = (
        await session.execute(
            select(
                WcsWikiPageRow.slug,
                WcsWikiPageRow.name,
                WcsWikiPageRow.kind,
                WcsWikiPageRow.document["aliases"],
                WcsWikiPageRow.document["definition"].astext,
                WcsWikiPageRow.document["in_practice"].astext,
                WcsWikiPageRow.document["details"],
            )
        )
    ).all()
    if not rows:
        return {"total": 0, "embedded": 0, "skipped": 0}

    flats: dict[str, str] = {}
    shas: dict[str, str] = {}
    for slug, name, kind, aliases, definition, in_practice, details in rows:
        flat = flatten_page(
            name=name,
            kind=kind,
            aliases=[str(a) for a in (aliases or [])],
            definition=definition or "",
            in_practice=in_practice or "",
            details=[
                f"{d.get('name', '')}: {d.get('gist', '')}".strip(": ")
                for d in (details or [])
                if isinstance(d, dict)
            ],
        )
        flats[slug] = flat
        shas[slug] = hashlib.sha256(flat.encode("utf-8")).hexdigest()

    existing = {
        slug: sha
        for slug, sha in (
            await session.execute(
                select(
                    WcsWikiPageEmbedding.slug, WcsWikiPageEmbedding.content_sha
                ).where(
                    WcsWikiPageEmbedding.embedding_model == embedding_model,
                    WcsWikiPageEmbedding.page_flattener_version
                    == page_flattener_version,
                )
            )
        ).all()
    }
    pending = sorted(slug for slug in flats if existing.get(slug) != shas[slug])
    if not pending:
        return {"total": len(flats), "embedded": 0, "skipped": len(flats)}

    vectors = await embedder.embed([flats[slug] for slug in pending])
    if len(vectors) != len(pending):
        raise RuntimeError(
            f"Embedder returned {len(vectors)} vectors for {len(pending)} pages"
        )
    await session.execute(
        delete(WcsWikiPageEmbedding).where(
            WcsWikiPageEmbedding.slug.in_(pending),
            WcsWikiPageEmbedding.embedding_model == embedding_model,
            WcsWikiPageEmbedding.page_flattener_version == page_flattener_version,
        )
    )
    for slug, vec in zip(pending, vectors, strict=True):
        session.add(
            WcsWikiPageEmbedding(
                slug=slug,
                embedding=vec,
                embedding_model=embedding_model,
                page_flattener_version=page_flattener_version,
                content_sha=shas[slug],
            )
        )
    await session.commit()
    return {
        "total": len(flats),
        "embedded": len(pending),
        "skipped": len(flats) - len(pending),
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
        select(WcsTranscriptChunk.transcript_id, WcsTranscriptChunk.content_sha)
        .where(
            WcsTranscriptChunk.embedding_model == embedding_model,
            WcsTranscriptChunk.chunking_version == chunking_version,
        )
        .distinct()
    )
    existing_per_transcript: dict[uuid.UUID, str] = {}
    for row in existing_q.all():
        existing_per_transcript[row[0]] = row[1]

    pending = [
        t for t in transcripts if existing_per_transcript.get(t.id) != shas[t.id]
    ]
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
    """The title a transcript's chunks are embedded under.

    The lesson's title (its ``WcsSource``), so a search for a lesson's
    subject finds its chunks; the transcript's source filename when the
    lesson has no title or the transcript no lesson.
    """
    titles = (
        (
            await session.execute(
                select(WcsSource.title)
                .where(WcsSource.transcript_id == transcript.id)
                .order_by(WcsSource.id)
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    if titles and titles[0]:
        return titles[0]
    return transcript.source_filename
