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
from typing import Literal, TypeGuard

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import WcsSource, WcsTranscript, WcsTranscriptChunk
from ...retrieval.wcs.queries import (
    fetch_visible_source,
    fetch_visible_source_for_transcript,
)
from ...retrieval.wcs.schemas import ToolError
from ...retrieval.wcs.tools import lesson_title, lesson_url
from ...retrieval.wcs.wiki_pages import get_wiki_page

SENTINEL_RE = re.compile(
    r"\[\[CITATIONS_BEGIN\]\](.*?)\[\[CITATIONS_END\]\]",
    re.DOTALL,
)

VALID_TYPES = {"lesson", "chunk", "page"}


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

    `transcript_id` is populated only for type=chunk citations. A lesson or
    chunk citation carries its lesson's metadata and points at the lesson's
    page. JSON serialization uses `exclude_none=True` so absent optional
    fields don't appear in the response.
    """

    marker: int = Field(
        ...,
        description=(
            "Inline citation marker (1-based) that ties this entry back to a "
            "``[N]`` reference in the answer text."
        ),
    )
    type: Literal["lesson", "chunk", "page"] = Field(
        ...,
        description=(
            "Citation kind. ``lesson`` points at a lesson (a WCS source); "
            "``chunk`` points at a passage of a lesson's transcript; ``page`` "
            "points at a wiki page."
        ),
    )
    id: str = Field(
        ...,
        description=(
            "Stable identifier of the cited resource. The source's UUID for lessons; "
            "``<transcript_uuid>:<chunk_index>`` for chunks; the page's slug "
            "for pages."
        ),
    )
    transcript_id: str | None = Field(
        None,
        description=(
            "UUID of the transcript a chunk citation belongs to. None for "
            "lesson and page citations."
        ),
    )
    title: str | None = Field(
        None,
        description=(
            "Display title for the cited resource — the lesson's title, or its "
            "transcript's source filename when it has none; a page's display "
            "name."
        ),
    )
    kind: str | None = Field(
        None,
        description=(
            "For ``type=page``: concept, technique, pattern or drill. None "
            "for lessons and chunks."
        ),
    )
    session_date: dt.date | None = Field(
        None,
        description=(
            "Session date of the underlying lesson, if known. Useful for the "
            "client to render a date alongside the citation."
        ),
    )
    # Lesson metadata mirrored from the lesson (the source; for a chunk, its
    # transcript's lesson). These let the client
    # render citations using the same label process as the /notes page:
    # date + session-type badge, primary label = students (for private
    # lessons) or organization, with "[with instructors]" appended.
    session_type: (
        Literal["private_lesson", "group_class", "correction", "other"] | None
    ) = Field(
        None,
        description=(
            "Session type of the lesson. None for pages, or a value outside "
            "the known set."
        ),
    )
    instructors: list[str] = Field(
        default_factory=list,
        description=("Instructors on the lesson. Empty for pages."),
    )
    students: list[str] = Field(
        default_factory=list,
        description=("Students on the lesson. Empty for pages."),
    )
    organization: str | None = Field(
        None,
        description="Organization on the lesson. None for pages, or when unrecorded.",
    )
    source_url: str | None = Field(
        None,
        description=(
            "Public URL where the cited resource can be read: the lesson's "
            "page for lessons and chunks, the wiki page for pages."
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
    site_url: str,
) -> tuple[list[EnrichedCitation], list[str]]:
    """Enrich each entry with display metadata; drop invalid or invisible IDs.

    Returns (enriched, dropped_ids). Dropped IDs cover: a malformed id, a
    lesson or chunk that does not exist, a lesson (or a chunk's lesson) the
    viewer may not see, and a page with no wiki page or none of it visible.
    """
    enriched: list[EnrichedCitation] = []
    dropped: list[str] = []

    for entry in entries:
        ent_id = entry["id"]
        ent_type = entry["type"]
        marker = entry["marker"]

        if ent_type == "page":
            try:
                page = await get_wiki_page(
                    session=session,
                    viewer_id=viewer_id,
                    site_url=site_url,
                    name=ent_id,
                )
            except ToolError:
                dropped.append(ent_id)
                continue
            enriched.append(
                EnrichedCitation(
                    marker=marker,
                    type="page",
                    id=page.slug,
                    title=page.name,
                    kind=page.kind,
                    source_url=page.source_url,
                )
            )
            continue

        if ent_type == "lesson":
            resolved = await _resolve_lesson(session, ent_id, viewer_id)
            transcript_id = None
        else:  # chunk
            resolved = await _resolve_chunk(session, ent_id, viewer_id)
            transcript_id = resolved[1].id if resolved is not None else None
        if resolved is None:
            dropped.append(ent_id)
            continue
        source, transcript = resolved
        enriched.append(
            EnrichedCitation(
                marker=marker,
                type=ent_type,
                id=ent_id if ent_type == "chunk" else str(source.id),
                transcript_id=str(transcript_id) if transcript_id else None,
                title=lesson_title(source, transcript),
                session_date=source.session_date,
                session_type=(
                    source.session_type
                    if _is_session_type(source.session_type)
                    else None
                ),
                instructors=list(source.instructors_raw or []),
                students=list(source.students_raw or []),
                organization=source.organization or None,
                source_url=lesson_url(site_url, source.id),
            )
        )

    return enriched, dropped


_SessionType = Literal["private_lesson", "group_class", "correction", "other"]
_SESSION_TYPES: frozenset[str] = frozenset(
    {"private_lesson", "group_class", "correction", "other"}
)


def _is_session_type(value: str | None) -> TypeGuard[_SessionType]:
    """True when a stored session_type is one of those the API serves.

    The column is free text, so a value outside the set is possible; it is
    dropped to None here rather than carried into a response model that
    rejects it.
    """
    return value in _SESSION_TYPES


async def _resolve_lesson(
    session: AsyncSession, source_id_str: str, viewer_id: str
) -> tuple[WcsSource, WcsTranscript | None] | None:
    """A lesson citation's source and transcript, if the viewer may see it."""
    try:
        source_id = uuid.UUID(source_id_str)
    except (ValueError, TypeError):
        return None
    source = await fetch_visible_source(session, source_id, viewer_id)
    if source is None:
        return None
    return source, await session.get(WcsTranscript, source.transcript_id)


async def _resolve_chunk(
    session: AsyncSession, chunk_id: str, viewer_id: str
) -> tuple[WcsSource, WcsTranscript] | None:
    """A chunk citation's lesson and transcript, if the viewer may see it."""
    transcript_id_str, _, idx_str = chunk_id.partition(":")
    if not transcript_id_str or not idx_str:
        return None
    try:
        transcript_uuid = uuid.UUID(transcript_id_str)
        int(idx_str)
    except (ValueError, TypeError):
        return None

    chunk = (
        (
            await session.execute(
                select(WcsTranscriptChunk).where(
                    WcsTranscriptChunk.chunk_id == chunk_id
                )
            )
        )
        .scalars()
        .first()
    )
    if chunk is None or chunk.transcript_id != transcript_uuid:
        return None
    transcript = await session.get(WcsTranscript, transcript_uuid)
    if transcript is None:
        return None
    source = await fetch_visible_source_for_transcript(
        session, transcript_uuid, viewer_id
    )
    if source is None:
        return None
    return source, transcript
