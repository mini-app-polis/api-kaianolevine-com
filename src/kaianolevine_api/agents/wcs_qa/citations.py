"""Sentinel-delimited citation parsing and DB-backed enrichment.

The model emits IDs only inside [[CITATIONS_BEGIN]] ... [[CITATIONS_END]];
this module locates that block, parses it, validates each ID exists and is
visible to the caller, and enriches with display metadata + source_url. The
visible answer text has the entire sentinel block stripped before reaching
the user — inline `[N]` markers stay.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import WcsNote, WcsTranscript, WcsTranscriptChunk
from ...services.wcs_access import user_can_see_note

SENTINEL_RE = re.compile(
    r"\[\[CITATIONS_BEGIN\]\](.*?)\[\[CITATIONS_END\]\]",
    re.DOTALL,
)

VALID_TYPES = {"note", "chunk"}


@dataclass
class ParsedCitations:
    """Successfully parsed citation block.

    ``raw_entries`` is the raw list of ``{marker, type, id}`` dicts as
    decoded from the sentinel-delimited JSON block; enrichment happens
    in a later pass. ``text_without_block`` is the model's answer with
    the entire sentinel block removed (the inline ``[N]`` markers stay
    in place).
    """

    raw_entries: list[dict]
    text_without_block: str


@dataclass
class CitationParseError:
    """Failure reason when the citation sentinel block cannot be parsed.

    ``code`` is a small enum used by the agent loop to decide whether to
    retry once with a corrective message; ``message`` is a human-readable
    explanation logged for debugging.
    """

    code: Literal["missing_block", "invalid_json", "invalid_entries"]
    message: str


class EnrichedCitation(BaseModel):
    """Final citation shape rendered into the API response.

    `transcript_id` is populated only for type=chunk citations. `source_url`
    is None for chunk citations whose transcript has zero or multiple linked
    notes (no clean URL to point at in v1). JSON serialization uses
    `exclude_none=True` so absent optional fields don't appear in the response.
    """

    marker: int = Field(
        ...,
        description=(
            "Inline citation marker (1-based) that ties this entry back to a "
            "``[N]`` reference in the answer text."
        ),
    )
    type: Literal["note", "chunk"] = Field(
        ...,
        description=(
            "Citation kind. ``note`` points at a structured WCS lesson note; "
            "``chunk`` points at a transcript chunk."
        ),
    )
    id: str = Field(
        ...,
        description=(
            "Stable identifier of the cited resource. UUID for notes; "
            "``<transcript_uuid>:<chunk_index>`` for chunks."
        ),
    )
    transcript_id: str | None = Field(
        None,
        description=(
            "UUID of the transcript a chunk citation belongs to. Always None "
            "for ``type=note`` citations."
        ),
    )
    title: str | None = Field(
        None,
        description=(
            "Display title for the cited resource — the linked note's title "
            "when one exists, otherwise the transcript's source filename."
        ),
    )
    session_date: dt.date | None = Field(
        None,
        description=(
            "Session date of the underlying lesson, if known. Useful for the "
            "client to render a date alongside the citation."
        ),
    )
    # Lesson metadata mirrored from the underlying note (or, for chunks, the
    # single linked note when there is exactly one). These let the client
    # render citations using the same label process as the /notes page:
    # date + session-type badge, primary label = students (for private
    # lessons) or organization, with "[with instructors]" appended.
    session_type: Literal["private_lesson", "group_class", "other"] | None = Field(
        None,
        description=(
            "Session type from the underlying note. None for chunk citations "
            "with no linked note."
        ),
    )
    instructors: list[str] = Field(
        default_factory=list,
        description=(
            "Instructors on the underlying note. Empty list when no linked note."
        ),
    )
    students: list[str] = Field(
        default_factory=list,
        description=(
            "Students on the underlying note. Empty list when no linked note."
        ),
    )
    organization: str | None = Field(
        None,
        description=(
            "Organization on the underlying note. None when no linked note."
        ),
    )
    source_url: str | None = Field(
        None,
        description=(
            "Public URL where the cited resource can be read. None for chunk "
            "citations whose transcript has zero or multiple linked notes "
            "(no clean v1 target)."
        ),
    )

    model_config = ConfigDict(extra="forbid")


def parse_citations_block(text: str) -> ParsedCitations | CitationParseError:
    """Locate, parse, and validate the sentinel block. Doesn't touch the DB.

    Returns ParsedCitations on success or a CitationParseError describing
    which validation step failed. text_without_block has the entire sentinel
    block (including the sentinels) removed.
    """
    match = SENTINEL_RE.search(text)
    if not match:
        return CitationParseError(
            code="missing_block",
            message="No [[CITATIONS_BEGIN]] ... [[CITATIONS_END]] block found",
        )

    inner = match.group(1).strip()
    try:
        entries = json.loads(inner)
    except json.JSONDecodeError as e:
        return CitationParseError(
            code="invalid_json", message=f"Citation block is not valid JSON: {e}"
        )

    if not isinstance(entries, list):
        return CitationParseError(
            code="invalid_entries",
            message="Citation block must be a JSON array",
        )

    for entry in entries:
        if not isinstance(entry, dict):
            return CitationParseError(
                code="invalid_entries",
                message=f"Citation entry is not a dict: {entry!r}",
            )
        if not all(k in entry for k in ("marker", "type", "id")):
            return CitationParseError(
                code="invalid_entries",
                message=f"Citation entry missing required keys: {entry!r}",
            )
        if not isinstance(entry["marker"], int):
            return CitationParseError(
                code="invalid_entries",
                message=f"marker must be an integer: {entry!r}",
            )
        if entry["type"] not in VALID_TYPES:
            return CitationParseError(
                code="invalid_entries",
                message=f"unknown citation type: {entry.get('type')!r}",
            )
        if not isinstance(entry["id"], str) or not entry["id"]:
            return CitationParseError(
                code="invalid_entries",
                message=f"id must be a non-empty string: {entry!r}",
            )

    text_without_block = SENTINEL_RE.sub("", text).rstrip()
    return ParsedCitations(raw_entries=entries, text_without_block=text_without_block)


async def enrich_citations(
    *,
    session: AsyncSession,
    entries: list[dict],
    viewer_id: str,
    owner_id: str,
    site_url: str,
) -> tuple[list[EnrichedCitation], list[str]]:
    """Enrich each entry with display metadata; drop invalid or invisible IDs.

    Returns (enriched, dropped_ids). Dropped IDs cover: malformed UUID,
    note not in DB, note not visible to viewer, chunk not in DB, chunk owned
    by another user.
    """
    enriched: list[EnrichedCitation] = []
    dropped: list[str] = []

    for entry in entries:
        ent_id = entry["id"]
        ent_type = entry["type"]
        marker = entry["marker"]

        if ent_type == "note":
            note = await _resolve_note(session, ent_id, viewer_id)
            if note is None:
                dropped.append(ent_id)
                continue
            enriched.append(
                EnrichedCitation(
                    marker=marker,
                    type="note",
                    id=ent_id,
                    title=note.title,
                    session_date=note.session_date,
                    session_type=note.session_type,
                    instructors=list(note.instructors or []),
                    students=list(note.students or []),
                    organization=note.organization or None,
                    source_url=_note_url(site_url, note.id),
                )
            )
        else:  # chunk
            resolved = await _resolve_chunk(session, ent_id, owner_id)
            if resolved is None:
                dropped.append(ent_id)
                continue
            source_url = (
                _note_url(site_url, resolved.linked_note_id)
                if resolved.linked_note_id is not None
                else None
            )
            enriched.append(
                EnrichedCitation(
                    marker=marker,
                    type="chunk",
                    id=ent_id,
                    transcript_id=str(resolved.transcript_id),
                    title=resolved.title,
                    session_date=resolved.session_date,
                    session_type=resolved.session_type,
                    instructors=resolved.instructors,
                    students=resolved.students,
                    organization=resolved.organization,
                    source_url=source_url,
                )
            )

    return enriched, dropped


async def _resolve_note(
    session: AsyncSession, note_id_str: str, viewer_id: str
) -> WcsNote | None:
    try:
        note_uuid = uuid.UUID(note_id_str)
    except (ValueError, TypeError):
        return None
    note = await session.get(WcsNote, note_uuid)
    if note is None:
        return None
    if not await user_can_see_note(session, viewer_id, note):
        return None
    return note


@dataclass
class _ChunkResolution:
    """Result of resolving a chunk citation against the DB.

    Lesson-metadata fields (``session_type``, ``instructors``, ``students``,
    ``organization``) are populated only when the chunk's transcript has
    exactly one linked note (and ``linked_note_id`` is set); they are empty
    / None otherwise. Callers should treat those as "no linked lesson
    context" and fall back to the transcript filename.
    """

    transcript_id: uuid.UUID
    chunk_index: int
    title: str
    session_date: dt.date | None
    linked_note_id: uuid.UUID | None
    session_type: Literal["private_lesson", "group_class", "other"] | None
    instructors: list[str]
    students: list[str]
    organization: str | None


async def _resolve_chunk(
    session: AsyncSession, chunk_id: str, owner_id: str
) -> _ChunkResolution | None:
    """Resolve a chunk citation against the DB.

    ``linked_note_id`` and the lesson-metadata fields are populated only when
    the transcript has exactly one linked note — that's the chunk's
    ``source_url`` target and the source for lesson display labels.
    """
    transcript_id_str, _, idx_str = chunk_id.partition(":")
    if not transcript_id_str or not idx_str:
        return None
    try:
        transcript_uuid = uuid.UUID(transcript_id_str)
        chunk_index = int(idx_str)
    except (ValueError, TypeError):
        return None

    chunk_q = await session.execute(
        select(WcsTranscriptChunk).where(WcsTranscriptChunk.chunk_id == chunk_id)
    )
    chunk = chunk_q.scalars().first()
    if chunk is None or chunk.owner_id != owner_id:
        return None

    transcript = await session.get(WcsTranscript, transcript_uuid)
    note_q = await session.execute(
        select(WcsNote).where(WcsNote.transcript_id == transcript_uuid)
    )
    notes = list(note_q.scalars().all())
    if len(notes) == 1:
        single_note = notes[0]
        title = single_note.title or (
            transcript.source_filename if transcript is not None else chunk_id
        )
        return _ChunkResolution(
            transcript_id=transcript_uuid,
            chunk_index=chunk_index,
            title=title,
            session_date=single_note.session_date,
            linked_note_id=single_note.id,
            session_type=single_note.session_type,
            instructors=list(single_note.instructors or []),
            students=list(single_note.students or []),
            organization=single_note.organization or None,
        )

    title = transcript.source_filename if transcript is not None else chunk_id
    return _ChunkResolution(
        transcript_id=transcript_uuid,
        chunk_index=chunk_index,
        title=title,
        session_date=None,
        linked_note_id=None,
        session_type=None,
        instructors=[],
        students=[],
        organization=None,
    )


def _note_url(site_url: str, note_id: uuid.UUID) -> str:
    return f"{site_url.rstrip('/')}/notes/{note_id}"
