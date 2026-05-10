"""The four WCS Q&A retrieval tools.

Pure-ish functions: each takes session + embedder + viewer/owner identity +
config knobs, returns Pydantic models from schemas.py. The agent loop wraps
these with closures so the model never sees the session or owner_id.

Visibility:
  - Notes:       filtered via the same rules as services.wcs_access.user_can_see_note
                 (default-visible OR admin OR explicit grant).
  - Transcripts: owner-scoped. No grant model in v1.

Cross-visibility IDs (note exists but not visible; transcript chunk owned by
another user) raise ToolError("not_found") to avoid existence leaks.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ...services.wcs_access import user_can_see_note
from .convergence import Embedder
from .flatten import flatten_note
from .queries import (
    fetch_linked_note_for_transcript,
    fetch_note,
    fetch_transcript,
    fetch_transcript_chunks,
    search_notes_db,
    search_transcripts_db,
)
from .schemas import (
    Note,
    NoteFilters,
    NoteHit,
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


def _note_url(site_url: str, note_id: uuid.UUID) -> str:
    return f"{site_url.rstrip('/')}/notes/{note_id}"


async def search_notes(
    *,
    session: AsyncSession,
    embedder: Embedder,
    viewer_id: str,
    embedding_model: str,
    flattener_version: int,
    query: str,
    k: int = K_DEFAULT,
    filters: NoteFilters | None = None,
) -> list[NoteHit]:
    """Vector search over wcs_notes flattened embeddings.

    Returns up to k hits visible to viewer, ordered by cosine similarity.
    No source_url on hits — citation requires having read the source.
    """
    k = _clamp_k(k)
    vectors = await embedder.embed([query])
    query_vec = vectors[0]

    rows = await search_notes_db(
        session=session,
        query_vec=query_vec,
        k=k,
        filters=filters,
        viewer_id=viewer_id,
        embedding_model=embedding_model,
        flattener_version=flattener_version,
    )

    hits: list[NoteHit] = []
    for note, score in rows:
        flat = flatten_note(
            title=note.title,
            instructors=list(note.instructors or []),
            students=list(note.students or []),
            organization=note.organization,
            session_date=note.session_date,
            notes_json=note.notes_json or {},
        )
        hits.append(
            NoteHit(
                note_id=str(note.id),
                title=note.title,
                session_date=note.session_date,
                session_type=note.session_type,
                instructors=list(note.instructors or []),
                students=list(note.students or []),
                organization=note.organization,
                snippet=_snippet(flat),
                score=score,
            )
        )
    return hits


async def search_transcripts(
    *,
    session: AsyncSession,
    embedder: Embedder,
    viewer_id: str,
    owner_id: str,
    embedding_model: str,
    chunking_version: int,
    query: str,
    k: int = K_DEFAULT,
    filters: TranscriptFilters | None = None,
) -> list[TranscriptChunkHit]:
    """Vector search over wcs_transcript_chunks. Owner-scoped (no grant model)."""
    k = _clamp_k(k)
    vectors = await embedder.embed([query])
    query_vec = vectors[0]

    rows = await search_transcripts_db(
        session=session,
        query_vec=query_vec,
        k=k,
        filters=filters,
        viewer_id=viewer_id,
        owner_id=owner_id,
        embedding_model=embedding_model,
        chunking_version=chunking_version,
    )

    hits: list[TranscriptChunkHit] = []
    for chunk, transcript, note, score in rows:
        if note is not None and note.title:
            transcript_title = note.title
        else:
            transcript_title = transcript.source_filename
        instructors = list(note.instructors or []) if note is not None else []
        session_date = note.session_date if note is not None else None
        hits.append(
            TranscriptChunkHit(
                chunk_id=chunk.chunk_id,
                transcript_id=str(chunk.transcript_id),
                transcript_title=transcript_title,
                session_date=session_date,
                instructors=instructors,
                chunk_index=chunk.chunk_index,
                start_offset=chunk.start_offset,
                snippet=_snippet(chunk.text),
                score=score,
            )
        )
    return hits


async def get_note(
    *,
    session: AsyncSession,
    viewer_id: str,
    site_url: str,
    note_id: uuid.UUID,
) -> Note:
    """Fetch a full note by id. Raises ToolError("not_found") if not visible."""
    note = await fetch_note(session, note_id)
    if note is None:
        raise ToolError("not_found", f"Note {note_id} not found")
    if not await user_can_see_note(session, viewer_id, note):
        raise ToolError("not_found", f"Note {note_id} not found")

    return Note(
        note_id=str(note.id),
        title=note.title,
        session_date=note.session_date,
        session_type=note.session_type,
        instructors=list(note.instructors or []),
        students=list(note.students or []),
        organization=note.organization,
        notes_json=dict(note.notes_json or {}),
        source_url=_note_url(site_url, note.id),
    )


async def get_transcript_window(
    *,
    session: AsyncSession,
    owner_id: str,
    site_url: str,
    embedding_model: str,
    chunking_version: int,
    chunk_id: str,
    before: int = 1,
    after: int = 1,
) -> TranscriptWindow:
    """Return a contiguous window of chunks centered on chunk_id.

    chunk_id format: "<transcript_uuid>:<chunk_index>".
    Owner-scoped — chunks owned by another user return not_found.
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
    if transcript is None or transcript.owner_id != owner_id:
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

    note = await fetch_linked_note_for_transcript(session, transcript_id)
    if note is not None:
        transcript_title = note.title or transcript.source_filename
        session_date = note.session_date
        source_url: str | None = _note_url(site_url, note.id)
    else:
        transcript_title = transcript.source_filename
        session_date = None
        source_url = None

    return TranscriptWindow(
        transcript_id=str(transcript_id),
        transcript_title=transcript_title,
        session_date=session_date,
        chunks=[
            TranscriptWindowChunk(
                chunk_id=c.chunk_id,
                chunk_index=c.chunk_index,
                text=c.text,
            )
            for c in selected
        ],
        source_url=source_url,
    )
