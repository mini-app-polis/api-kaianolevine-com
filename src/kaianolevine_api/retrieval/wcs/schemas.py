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
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    instructors: list[str] | None = None
    session_type: str | None = None
    organization: str | None = None

    model_config = ConfigDict(extra="forbid")


class TranscriptFilters(BaseModel):
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    instructors: list[str] | None = None

    model_config = ConfigDict(extra="forbid")


class NoteHit(BaseModel):
    """Search hit. No source_url — citation requires having read the source."""

    note_id: str
    title: str | None
    session_date: dt.date | None
    session_type: str
    instructors: list[str]
    students: list[str]
    organization: str
    snippet: str
    score: float


class Note(BaseModel):
    """Full note returned by get_note. Includes source_url and notes_json."""

    note_id: str
    title: str | None
    session_date: dt.date | None
    session_type: str
    instructors: list[str]
    students: list[str]
    organization: str
    notes_json: dict[str, Any]
    source_url: str


class TranscriptChunkHit(BaseModel):
    """Search hit for a transcript chunk. No source_url."""

    chunk_id: str
    transcript_id: str
    transcript_title: str
    session_date: dt.date | None
    instructors: list[str]
    chunk_index: int
    start_offset: int
    snippet: str
    score: float


class TranscriptWindowChunk(BaseModel):
    chunk_id: str
    chunk_index: int
    text: str


class TranscriptWindow(BaseModel):
    """Window of contiguous chunks around a target chunk_id."""

    transcript_id: str
    transcript_title: str
    session_date: dt.date | None
    chunks: list[TranscriptWindowChunk] = Field(default_factory=list)
    source_url: str


class ToolError(Exception):
    """Tool-level error. Caught by the agent loop and serialized as JSON."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)
