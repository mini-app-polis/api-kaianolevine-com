"""Pydantic schemas for the WCS Q&A retrieval tool surface.

These are the shapes the lesson tools (search_transcripts,
get_transcript_window) and the wiki tools accept and return. They are also
what the agent loop sees when serializing tool results back to the model.

A lesson is a ``WcsSource``; its metadata (title, date, type, instructors,
students, organization) comes from the source, and its page on the site is
``/notes/detail?id=<source id>``.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class TranscriptFilters(BaseModel):
    """Optional metadata filters for ``search_transcripts``.

    Each reads the chunk's lesson (its ``WcsSource``).
    """

    date_from: dt.date | None = Field(
        None,
        description=("Inclusive lower bound on the lesson's session_date."),
    )
    date_to: dt.date | None = Field(
        None,
        description=("Inclusive upper bound on the lesson's session_date."),
    )
    instructors: list[str] | None = Field(
        None,
        description=(
            "Restrict to lessons whose instructors overlap with any of these names."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class TranscriptChunkHit(BaseModel):
    """Search hit for a transcript chunk, with its lesson. No source_url."""

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
    source_id: str = Field(
        ...,
        description=(
            "UUID of the lesson (``WcsSource``) the transcript belongs to; the "
            "id a ``lesson`` citation takes."
        ),
    )
    transcript_title: str = Field(
        ...,
        description=(
            "Display title — the lesson's title, or the transcript's source "
            "filename when the lesson has none."
        ),
    )
    session_date: dt.date | None = Field(
        ..., description="Session date of the lesson, when known."
    )
    session_type: str | None = Field(
        None, description="Session type of the lesson (private_lesson, group_class, …)."
    )
    instructors: list[str] = Field(
        ..., description="Instructor names on the lesson, as recorded."
    )
    students: list[str] = Field(
        default_factory=list, description="Student names on the lesson, as recorded."
    )
    organization: str | None = Field(
        None, description="Organization the lesson was with, when recorded."
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
    """Window of contiguous chunks around a target chunk_id, with its lesson.

    ``source_url`` is the lesson's page on the site, where the whole lesson
    can be read.
    """

    transcript_id: str = Field(
        ..., description="UUID of the transcript this window belongs to."
    )
    source_id: str = Field(
        ...,
        description=(
            "UUID of the lesson (``WcsSource``); the id a ``lesson`` citation takes."
        ),
    )
    transcript_title: str = Field(
        ...,
        description=(
            "Display title — the lesson's title, or the transcript's source "
            "filename when the lesson has none."
        ),
    )
    session_date: dt.date | None = Field(
        ..., description="Session date of the lesson, when known."
    )
    session_type: str | None = Field(None, description="Session type of the lesson.")
    instructors: list[str] = Field(
        default_factory=list, description="Instructor names on the lesson."
    )
    students: list[str] = Field(
        default_factory=list, description="Student names on the lesson."
    )
    organization: str | None = Field(
        None, description="Organization the lesson was with, when recorded."
    )
    chunks: list[TranscriptWindowChunk] = Field(
        default_factory=list,
        description=(
            "Contiguous chunks that make up the window, ordered by "
            "``chunk_index`` ascending."
        ),
    )
    source_url: str = Field(
        ..., description="The lesson's page on the site: /notes/detail?id=<source id>."
    )


class WikiPageHit(BaseModel):
    """One ``search_wiki`` hit: a wiki page the question may be about.

    No ``source_url`` — read the page with ``get_wiki_page`` before citing it.
    """

    slug: str = Field(..., description="The page's slug; pass it to get_wiki_page.")
    name: str = Field(..., description="The page's display name.")
    kind: str = Field(..., description="concept, technique, pattern or drill.")
    snippet: str = Field(..., description="The start of the page's definition.")
    score: float = Field(
        ..., description="Cosine similarity; a relative ordering signal only."
    )

    model_config = ConfigDict(extra="forbid")


class WikiPageDoc(BaseModel):
    """A wiki page as ``get_wiki_page`` returns it to the agent.

    The page's own text, filtered to what the asker may see. ``details``,
    ``mistakes`` and ``related`` are one line each. The lessons behind the
    page are found through ``search_transcripts`` and cited as lessons or
    chunks, not from here.
    """

    slug: str = Field(..., description="The page's slug; cite it as type page.")
    name: str = Field(..., description="The page's display name.")
    kind: str = Field(..., description="concept, technique, pattern or drill.")
    aliases: list[str] = Field(default_factory=list, description="Other names.")
    definition: str = Field("", description="What it is, in a sentence or two.")
    in_practice: str = Field("", description="What a dancer needs beyond that.")
    details: list[str] = Field(
        default_factory=list, description="Narrower ideas folded into the page."
    )
    mistakes: list[str] = Field(
        default_factory=list, description="Mistake → correction lines."
    )
    related: list[str] = Field(
        default_factory=list, description="Related pages and how they relate."
    )
    teachers: list[str] = Field(
        default_factory=list, description="Teachers whose lessons the page draws on."
    )
    requested: str | None = Field(
        None,
        description=(
            "What was asked for, when it was another name for this page or a "
            "narrower idea folded into it."
        ),
    )
    source_url: str = Field(..., description="Where the page is read on the site.")

    model_config = ConfigDict(extra="forbid")


class ToolError(Exception):
    """Tool-level error. Caught by the agent loop and serialized as JSON."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)
