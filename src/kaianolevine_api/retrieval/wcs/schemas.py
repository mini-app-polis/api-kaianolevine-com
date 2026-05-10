"""Pydantic schemas for the WCS Q&A retrieval tool surface.

These are the shapes the four tools (search_notes, search_transcripts,
get_note, get_transcript_window) accept and return. They are also what the
agent loop sees when serializing tool results back to the model.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NoteFilters(BaseModel):
    """Optional metadata filters for ``search_notes``.

    All fields default to None (unfiltered). Date bounds are inclusive on
    both ends. ``instructors`` matches when any of the supplied names
    overlap with the note's ``instructors`` array.
    """

    date_from: dt.date | None = Field(
        None,
        description=(
            "Inclusive lower bound on session_date. Notes earlier than this "
            "date are excluded."
        ),
    )
    date_to: dt.date | None = Field(
        None,
        description=(
            "Inclusive upper bound on session_date. Notes later than this "
            "date are excluded."
        ),
    )
    instructors: list[str] | None = Field(
        None,
        description=(
            "Restrict to notes whose instructors array overlaps with any of "
            "these names."
        ),
    )
    session_type: str | None = Field(
        None,
        description=(
            "Restrict to notes with this exact session_type (e.g. "
            "``private_lesson``, ``group_class``)."
        ),
    )
    organization: str | None = Field(
        None,
        description="Restrict to notes with this exact organization label.",
    )

    model_config = ConfigDict(extra="forbid")


class TranscriptFilters(BaseModel):
    """Optional metadata filters for ``search_transcripts``.

    Date and instructor filters are joined through the linked
    ``WcsNote`` when present; transcripts without a linked note are
    excluded when any of these filters are set.
    """

    date_from: dt.date | None = Field(
        None,
        description=(
            "Inclusive lower bound on the linked note's session_date. "
            "Joined through the transcript's linked note when present."
        ),
    )
    date_to: dt.date | None = Field(
        None,
        description=(
            "Inclusive upper bound on the linked note's session_date. "
            "Joined through the transcript's linked note when present."
        ),
    )
    instructors: list[str] | None = Field(
        None,
        description=(
            "Restrict to transcripts whose linked note's instructors array "
            "overlaps with any of these names."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class NoteHit(BaseModel):
    """Search hit. No source_url — citation requires having read the source."""

    note_id: str = Field(..., description="UUID of the matched WCS note.")
    title: str | None = Field(
        ...,
        description=(
            "Display title for the note. May be None for notes ingested "
            "without a title."
        ),
    )
    session_date: dt.date | None = Field(
        ...,
        description="Session date of the underlying lesson, if recorded.",
    )
    session_type: str = Field(
        ...,
        description=(
            "Session type label — e.g. ``private_lesson``, ``group_class``, "
            "``workshop``."
        ),
    )
    instructors: list[str] = Field(
        ..., description="Instructor names associated with this note."
    )
    students: list[str] = Field(
        ...,
        description=(
            "Student names associated with this note. Empty list for "
            "group sessions filed under an organization."
        ),
    )
    organization: str = Field(
        ...,
        description=(
            "Organization label for the note (empty string for individual "
            "lesson notes)."
        ),
    )
    snippet: str = Field(
        ...,
        description=(
            "Short excerpt from the flattened note text, suitable for "
            "rendering as preview content above the full note."
        ),
    )
    score: float = Field(
        ...,
        description=(
            "Cosine-similarity score against the query embedding. Higher "
            "is closer; values are not calibrated across queries."
        ),
    )


class Note(BaseModel):
    """Full note returned by get_note. Includes source_url and notes_json."""

    note_id: str = Field(..., description="UUID of the WCS note.")
    title: str | None = Field(
        ...,
        description=(
            "Display title for the note. May be None for notes ingested "
            "without a title."
        ),
    )
    session_date: dt.date | None = Field(
        ...,
        description="Session date of the underlying lesson, if recorded.",
    )
    session_type: str = Field(
        ..., description="Session type label (see ``NoteHit.session_type``)."
    )
    instructors: list[str] = Field(
        ..., description="Instructor names associated with this note."
    )
    students: list[str] = Field(
        ..., description="Student names associated with this note."
    )
    organization: str = Field(..., description="Organization label for the note.")
    notes_json: dict[str, Any] = Field(
        ...,
        description=(
            "Full structured-notes payload as stored by the ingest pipeline "
            "(summary, key concepts, drills, etc.)."
        ),
    )
    source_url: str = Field(
        ...,
        description=(
            "Public URL where this note can be read. Always set when ``Note`` "
            "is returned from ``get_note`` (use this URL when citing)."
        ),
    )


class TranscriptChunkHit(BaseModel):
    """Search hit for a transcript chunk. No source_url."""

    chunk_id: str = Field(
        ...,
        description=(
            "Composite identifier of the form ``<transcript_uuid>:<chunk_index>``."
        ),
    )
    transcript_id: str = Field(
        ...,
        description="UUID of the transcript this chunk belongs to.",
    )
    transcript_title: str = Field(
        ...,
        description=(
            "Display title — the linked note's title when one exists, "
            "otherwise the transcript's source filename."
        ),
    )
    session_date: dt.date | None = Field(
        ...,
        description=(
            "Session date of the underlying lesson, when joinable through a "
            "linked note. None when the transcript has zero or multiple "
            "linked notes."
        ),
    )
    instructors: list[str] = Field(
        ...,
        description=(
            "Instructor names from the linked note. Empty when the transcript "
            "has no linked note."
        ),
    )
    chunk_index: int = Field(
        ...,
        description=(
            "Zero-based position of this chunk in the transcript's chunk sequence."
        ),
    )
    start_offset: int = Field(
        ...,
        description=(
            "Character offset of this chunk's start in the original "
            "transcript text. Used to assemble reading windows."
        ),
    )
    snippet: str = Field(
        ...,
        description=(
            "Short excerpt from the chunk text, suitable for preview "
            "rendering before the full chunk is fetched."
        ),
    )
    score: float = Field(
        ...,
        description=(
            "Cosine-similarity score against the query embedding. Higher is "
            "closer; values are not calibrated across queries."
        ),
    )


class TranscriptWindowChunk(BaseModel):
    """One chunk inside a ``TranscriptWindow`` reading window.

    Carries just enough to render the contiguous transcript region —
    no embedding, no offsets back into source. Returned ordered by
    ``chunk_index`` ascending alongside its sibling chunks.
    """

    chunk_id: str = Field(
        ...,
        description=(
            "Composite identifier of the form ``<transcript_uuid>:<chunk_index>``."
        ),
    )
    chunk_index: int = Field(
        ...,
        description=(
            "Zero-based position of this chunk in the transcript's chunk sequence."
        ),
    )
    text: str = Field(
        ...,
        description="Decoded chunk text, without any transcript-title prefix.",
    )


class TranscriptWindow(BaseModel):
    """Window of contiguous chunks around a target chunk_id.

    source_url points at the linked note's page (option b: chunks fall back
    to their underlying note rather than a dedicated transcript reader).
    None when the transcript has zero or multiple linked notes — those
    chunks have no v1 public page.
    """

    transcript_id: str = Field(
        ..., description="UUID of the transcript this window belongs to."
    )
    transcript_title: str = Field(
        ...,
        description=(
            "Display title — the linked note's title when one exists, "
            "otherwise the transcript's source filename."
        ),
    )
    session_date: dt.date | None = Field(
        ...,
        description=(
            "Session date of the linked note, when available; None when the "
            "transcript has zero or multiple linked notes."
        ),
    )
    chunks: list[TranscriptWindowChunk] = Field(
        default_factory=list,
        description=(
            "Contiguous chunks that make up the window, ordered by "
            "``chunk_index`` ascending."
        ),
    )
    source_url: str | None = Field(
        None,
        description=(
            "Public URL where the underlying note can be read. None when the "
            "transcript has zero or multiple linked notes (no clean v1 target)."
        ),
    )


class ToolError(Exception):
    """Tool-level error. Caught by the agent loop and serialized as JSON."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)
