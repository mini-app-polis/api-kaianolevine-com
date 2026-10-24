"""The WCS Q&A lesson tools: search a lesson's transcript, read a window of it.

Pure-ish functions: each takes session + embedder + viewer identity + config
knobs, returns Pydantic models from schemas.py. The agent loop wraps these
with closures so the model never sees the session. The wiki tools are in
wiki_pages.py.

Visibility is the lesson's (its ``WcsSource``), decided as the wiki decides
it: the whole corpus for a viewer with ``wcs.corpus.read``, otherwise the
default-visible lessons and those granted to the viewer.

A chunk whose lesson the viewer may not see raises ToolError("not_found"),
the same as one that does not exist, so a hidden lesson's existence does not
leak.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ...models import WcsSource, WcsTranscript
from .convergence import Embedder
from .queries import (
    fetch_transcript,
    fetch_transcript_chunks,
    fetch_visible_source_for_transcript,
    search_transcripts_db,
)
from .schemas import (
    ToolError,
    TranscriptChunkHit,
    TranscriptFilters,
    TranscriptWindow,
    TranscriptWindowChunk,
)

K_DEFAULT = 10
K_MAX = 25
SNIPPET_CHARS = 200


def _clamp_k(k: int) -> int:
    if k < 1:
        return 1
    if k > K_MAX:
        return K_MAX
    return k


def _snippet(text: str, n: int = SNIPPET_CHARS) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


def lesson_url(site_url: str, source_id: uuid.UUID) -> str:
    """The lesson's page on the site.

    /notes/detail?id=, not /notes/<id>. The site is a static build and cannot
    serve a path-shaped id without a rewrite; the rewrite that used to do it
    looped in production and was removed. The page reads a source id.
    """
    return f"{site_url.rstrip('/')}/notes/detail?id={source_id}"


def lesson_title(source: WcsSource, transcript: WcsTranscript | None) -> str:
    """The lesson's title, or its transcript's filename when it has none."""
    if source.title:
        return source.title
    if transcript is not None and transcript.source_filename:
        return transcript.source_filename
    return str(source.id)


async def search_transcripts(
    *,
    session: AsyncSession,
    embedder: Embedder,
    viewer_id: str,
    embedding_model: str,
    chunking_version: int,
    query: str,
    k: int = K_DEFAULT,
    filters: TranscriptFilters | None = None,
) -> list[TranscriptChunkHit]:
    """Vector search over the transcript chunks of lessons the viewer may see."""
    k = _clamp_k(k)
    vectors = await embedder.embed([query])
    query_vec = vectors[0]

    rows = await search_transcripts_db(
        session=session,
        query_vec=query_vec,
        k=k,
        filters=filters,
        viewer_id=viewer_id,
        embedding_model=embedding_model,
        chunking_version=chunking_version,
    )

    return [
        TranscriptChunkHit(
            chunk_id=chunk.chunk_id,
            transcript_id=str(chunk.transcript_id),
            source_id=str(source.id),
            transcript_title=lesson_title(source, transcript),
            session_date=source.session_date,
            session_type=source.session_type or None,
            instructors=list(source.instructors_raw or []),
            students=list(source.students_raw or []),
            organization=source.organization or None,
            chunk_index=chunk.chunk_index,
            start_offset=chunk.start_offset,
            snippet=_snippet(chunk.text),
            score=score,
        )
        for chunk, transcript, source, score in rows
    ]


async def get_transcript_window(
    *,
    session: AsyncSession,
    viewer_id: str,
    site_url: str,
    embedding_model: str,
    chunking_version: int,
    chunk_id: str,
    before: int = 1,
    after: int = 1,
) -> TranscriptWindow:
    """Return a contiguous window of chunks centered on chunk_id.

    chunk_id format: "<transcript_uuid>:<chunk_index>". A chunk whose lesson
    the viewer may not see is not found.
    """
    if before < 0 or after < 0:
        raise ToolError("invalid_input", "before/after must be non-negative")

    transcript_id_str, _, chunk_idx_str = chunk_id.partition(":")
    if not transcript_id_str or not chunk_idx_str:
        raise ToolError("invalid_input", f"Malformed chunk_id: {chunk_id!r}")
    try:
        transcript_id = uuid.UUID(transcript_id_str)
        target_idx = int(chunk_idx_str)
    except (ValueError, TypeError) as e:
        raise ToolError("invalid_input", f"Malformed chunk_id: {chunk_id!r}") from e

    transcript = await fetch_transcript(session, transcript_id)
    source = (
        await fetch_visible_source_for_transcript(session, transcript_id, viewer_id)
        if transcript is not None
        else None
    )
    if transcript is None or source is None:
        raise ToolError("not_found", f"Transcript {transcript_id} not found")

    chunks = await fetch_transcript_chunks(
        session,
        transcript_id,
        embedding_model=embedding_model,
        chunking_version=chunking_version,
    )
    if not chunks:
        raise ToolError("not_found", f"No chunks found for transcript {transcript_id}")

    indices = [c.chunk_index for c in chunks]
    if target_idx not in indices:
        raise ToolError("not_found", f"Chunk {chunk_id} not found")

    lo = max(target_idx - before, indices[0])
    hi = min(target_idx + after, indices[-1])
    selected = [c for c in chunks if lo <= c.chunk_index <= hi]

    return TranscriptWindow(
        transcript_id=str(transcript_id),
        source_id=str(source.id),
        transcript_title=lesson_title(source, transcript),
        session_date=source.session_date,
        session_type=source.session_type or None,
        instructors=list(source.instructors_raw or []),
        students=list(source.students_raw or []),
        organization=source.organization or None,
        chunks=[
            TranscriptWindowChunk(
                chunk_id=c.chunk_id,
                chunk_index=c.chunk_index,
                text=c.text,
            )
            for c in selected
        ],
        source_url=lesson_url(site_url, source.id),
    )
