"""Citation parsing + DB-backed enrichment for the WCS Q&A agent."""

from __future__ import annotations

import datetime as dt
import uuid as _uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.agents.wcs_qa.citations import (
    CitationParseError,
    ParsedCitations,
    enrich_citations,
    parse_citations_block,
)
from kaianolevine_api.models import LegacyWcsNote, WcsTranscript, WcsTranscriptChunk

SITE_URL = "https://wcs.example.com"


# ── Pure parsing tests ────────────────────────────────────────────────────────


def test_parses_valid_block() -> None:
    text_in = (
        "Kyle taught the anchor lands on 5-and-6 [1]. Sarah drilled it [2].\n\n"
        "[[CITATIONS_BEGIN]]\n"
        '[{"marker": 1, "type": "note", "id": "550e8400-e29b-41d4-a716-446655440000"},\n'
        ' {"marker": 2, "type": "chunk", "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479:4"}]\n'
        "[[CITATIONS_END]]"
    )
    parsed = parse_citations_block(text_in)
    assert isinstance(parsed, ParsedCitations)
    assert len(parsed.raw_entries) == 2
    assert parsed.raw_entries[0]["type"] == "note"
    assert parsed.raw_entries[1]["type"] == "chunk"
    # Inline markers preserved, sentinel block removed.
    assert "[1]" in parsed.text_without_block
    assert "[2]" in parsed.text_without_block
    assert "CITATIONS_BEGIN" not in parsed.text_without_block
    assert "CITATIONS_END" not in parsed.text_without_block


def test_parses_empty_array() -> None:
    text_in = "I don't have any sources for this.\n\n[[CITATIONS_BEGIN]]\n[]\n[[CITATIONS_END]]"
    parsed = parse_citations_block(text_in)
    assert isinstance(parsed, ParsedCitations)
    assert parsed.raw_entries == []


def test_missing_block_returns_error() -> None:
    err = parse_citations_block("Just an answer with no citation block.")
    assert isinstance(err, CitationParseError)
    assert err.code == "missing_block"


def test_invalid_json_returns_error() -> None:
    text_in = "answer\n\n[[CITATIONS_BEGIN]]\nnot valid json\n[[CITATIONS_END]]"
    err = parse_citations_block(text_in)
    assert isinstance(err, CitationParseError)
    assert err.code == "invalid_json"


def test_non_array_returns_error() -> None:
    text_in = '[[CITATIONS_BEGIN]]\n{"marker": 1, "type": "note", "id": "x"}\n[[CITATIONS_END]]'
    err = parse_citations_block(text_in)
    assert isinstance(err, CitationParseError)
    assert err.code == "invalid_entries"


def test_missing_keys_returns_error() -> None:
    text_in = '[[CITATIONS_BEGIN]]\n[{"marker": 1, "type": "note"}]\n[[CITATIONS_END]]'
    err = parse_citations_block(text_in)
    assert isinstance(err, CitationParseError)
    assert err.code == "invalid_entries"


def test_unknown_type_returns_error() -> None:
    text_in = (
        "[[CITATIONS_BEGIN]]\n"
        '[{"marker": 1, "type": "video", "id": "abc"}]\n'
        "[[CITATIONS_END]]"
    )
    err = parse_citations_block(text_in)
    assert isinstance(err, CitationParseError)
    assert err.code == "invalid_entries"


def test_marker_must_be_int() -> None:
    text_in = (
        "[[CITATIONS_BEGIN]]\n"
        '[{"marker": "1", "type": "note", "id": "abc"}]\n'
        "[[CITATIONS_END]]"
    )
    err = parse_citations_block(text_in)
    assert isinstance(err, CitationParseError)


def test_id_must_be_nonempty() -> None:
    text_in = (
        "[[CITATIONS_BEGIN]]\n"
        '[{"marker": 1, "type": "note", "id": ""}]\n'
        "[[CITATIONS_END]]"
    )
    err = parse_citations_block(text_in)
    assert isinstance(err, CitationParseError)


def test_block_in_middle_of_text_still_stripped() -> None:
    text_in = (
        "Some answer text.\n\n"
        "[[CITATIONS_BEGIN]]\n[]\n[[CITATIONS_END]]\n\n"
        "Trailing text — unusual but allowed by the parser."
    )
    parsed = parse_citations_block(text_in)
    assert isinstance(parsed, ParsedCitations)
    assert "CITATIONS_BEGIN" not in parsed.text_without_block
    assert "Trailing text" in parsed.text_without_block


# ── Enrichment tests (DB-backed) ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def seed_admin(reset_db, async_engine) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('dev-owner', '', '', 1) "
                "ON CONFLICT (user_id) DO UPDATE SET is_admin = excluded.is_admin"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('viewer', '', '', 0) ON CONFLICT (user_id) DO NOTHING"
            )
        )


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        yield session


async def _seed_note_with_chunk(
    session: AsyncSession,
) -> tuple[LegacyWcsNote, WcsTranscriptChunk]:
    transcript = WcsTranscript(
        owner_id="dev-owner",
        raw_text="some transcript text",
        source_type="plaud",
        source_filename="lesson.txt",
        drive_file_id="drive-1",
    )
    session.add(transcript)
    await session.commit()
    await session.refresh(transcript)

    note = LegacyWcsNote(
        owner_id="dev-owner",
        transcript_id=transcript.id,
        title="Anchor step",
        session_date=dt.date(2024, 3, 8),
        session_type="private_lesson",
        visibility="private",
        model="claude-sonnet-4-6",
        provider="anthropic",
        notes_json={"summary": "x"},
        instructors=["Kyle"],
        students=["Sarah"],
        organization="",
        is_default_visible=False,
    )
    session.add(note)

    chunk = WcsTranscriptChunk(
        chunk_id=f"{transcript.id}:0",
        embedding_model="text-embedding-3-small",
        chunking_version=1,
        transcript_id=transcript.id,
        owner_id="dev-owner",
        chunk_index=0,
        start_offset=0,
        end_offset=20,
        text="some transcript text",
        embedding=[0.0] * 1536,
        content_sha="x",
    )
    session.add(chunk)
    await session.commit()
    await session.refresh(note)
    await session.refresh(chunk)
    return note, chunk


async def test_enrich_note_citation(db_session) -> None:
    note, _ = await _seed_note_with_chunk(db_session)
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[{"marker": 1, "type": "note", "id": str(note.id)}],
        viewer_id="dev-owner",
        owner_id="dev-owner",
        site_url=SITE_URL,
    )
    assert dropped == []
    assert len(enriched) == 1
    c = enriched[0]
    assert c.type == "note"
    assert c.title == "Anchor step"
    assert c.session_date == dt.date(2024, 3, 8)
    assert c.source_url == f"{SITE_URL}/notes/{note.id}"
    assert c.transcript_id is None


async def test_enrich_chunk_citation(db_session) -> None:
    note, chunk = await _seed_note_with_chunk(db_session)
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[{"marker": 2, "type": "chunk", "id": chunk.chunk_id}],
        viewer_id="dev-owner",
        owner_id="dev-owner",
        site_url=SITE_URL,
    )
    assert dropped == []
    assert len(enriched) == 1
    c = enriched[0]
    assert c.type == "chunk"
    assert c.transcript_id == str(chunk.transcript_id)
    assert c.title == "Anchor step"
    # Option (b): chunk citations link to the linked note's page.
    assert c.source_url == f"{SITE_URL}/notes/{note.id}"


async def test_enrich_drops_unknown_note_id(db_session) -> None:
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[{"marker": 1, "type": "note", "id": str(_uuid.uuid4())}],
        viewer_id="dev-owner",
        owner_id="dev-owner",
        site_url=SITE_URL,
    )
    assert enriched == []
    assert len(dropped) == 1


async def test_enrich_drops_invisible_note(db_session) -> None:
    note, _ = await _seed_note_with_chunk(db_session)
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[{"marker": 1, "type": "note", "id": str(note.id)}],
        viewer_id="viewer",  # non-admin, no grant
        owner_id="viewer",
        site_url=SITE_URL,
    )
    assert enriched == []
    assert dropped == [str(note.id)]


async def test_enrich_drops_cross_owner_chunk(db_session) -> None:
    _, chunk = await _seed_note_with_chunk(db_session)
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[{"marker": 2, "type": "chunk", "id": chunk.chunk_id}],
        viewer_id="other-user",
        owner_id="other-user",
        site_url=SITE_URL,
    )
    assert enriched == []
    assert dropped == [chunk.chunk_id]


async def test_enrich_drops_malformed_uuid(db_session) -> None:
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[{"marker": 1, "type": "note", "id": "not-a-uuid"}],
        viewer_id="dev-owner",
        owner_id="dev-owner",
        site_url=SITE_URL,
    )
    assert enriched == []
    assert dropped == ["not-a-uuid"]


async def test_enrich_drops_malformed_chunk_id(db_session) -> None:
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[{"marker": 2, "type": "chunk", "id": "not-a-chunk-id"}],
        viewer_id="dev-owner",
        owner_id="dev-owner",
        site_url=SITE_URL,
    )
    assert enriched == []
    assert dropped == ["not-a-chunk-id"]


async def test_enrich_chunk_without_linked_note_has_null_source_url(
    db_session,
) -> None:
    """Transcripts with no linked note can't resolve to a public page in v1."""
    from kaianolevine_api.models import WcsTranscript, WcsTranscriptChunk

    transcript = WcsTranscript(
        owner_id="dev-owner",
        raw_text="orphan transcript",
        source_type="plaud",
        source_filename="orphan.txt",
        drive_file_id="d-orphan",
    )
    db_session.add(transcript)
    await db_session.commit()
    await db_session.refresh(transcript)

    chunk = WcsTranscriptChunk(
        chunk_id=f"{transcript.id}:0",
        embedding_model="text-embedding-3-small",
        chunking_version=1,
        transcript_id=transcript.id,
        owner_id="dev-owner",
        chunk_index=0,
        start_offset=0,
        end_offset=10,
        text="orphan",
        embedding=[0.0] * 1536,
        content_sha="x",
    )
    db_session.add(chunk)
    await db_session.commit()

    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[{"marker": 1, "type": "chunk", "id": chunk.chunk_id}],
        viewer_id="dev-owner",
        owner_id="dev-owner",
        site_url=SITE_URL,
    )
    assert dropped == []
    assert len(enriched) == 1
    assert enriched[0].source_url is None
    # Title falls back to source_filename when no linked note exists.
    assert enriched[0].title == "orphan.txt"


async def test_enrich_mixed_entries_some_drop_some_keep(db_session) -> None:
    note, chunk = await _seed_note_with_chunk(db_session)
    bogus_id = str(_uuid.uuid4())
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[
            {"marker": 1, "type": "note", "id": str(note.id)},
            {"marker": 2, "type": "note", "id": bogus_id},
            {"marker": 3, "type": "chunk", "id": chunk.chunk_id},
        ],
        viewer_id="dev-owner",
        owner_id="dev-owner",
        site_url=SITE_URL,
    )
    assert len(enriched) == 2
    assert dropped == [bogus_id]
    assert {c.marker for c in enriched} == {1, 3}
