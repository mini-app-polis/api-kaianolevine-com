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
from kaianolevine_api.models import (
    WcsSource,
    WcsSourceGrant,
    WcsTranscript,
    WcsTranscriptChunk,
)
from tests.integration.conftest import grant_role

SITE_URL = "https://wcs.example.com"


# ── Pure parsing tests ────────────────────────────────────────────────────────


def test_parses_valid_block() -> None:
    text_in = (
        "Kyle taught the anchor lands on 5-and-6 [1]. Sarah drilled it [2].\n\n"
        "[[CITATIONS_BEGIN]]\n"
        '[{"marker": 1, "type": "lesson", "id": "550e8400-e29b-41d4-a716-446655440000"},\n'
        ' {"marker": 2, "type": "chunk", "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479:4"}]\n'
        "[[CITATIONS_END]]"
    )
    parsed = parse_citations_block(text_in)
    assert isinstance(parsed, ParsedCitations)
    assert len(parsed.raw_entries) == 2
    assert parsed.raw_entries[0]["type"] == "lesson"
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


def test_the_retired_note_type_is_refused() -> None:
    """Lessons are cited as type lesson; the legacy notes are gone."""
    text_in = (
        "[[CITATIONS_BEGIN]]\n"
        '[{"marker": 1, "type": "note", "id": "550e8400-e29b-41d4-a716-446655440000"}]\n'
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
#
# Lessons are WcsSource rows; transcripts and chunks are owned by the
# transcription cog's machine principal, as in production. Three askers:
# dev-owner holds the whole corpus, granted has a grant on the private
# lesson, viewer sees only default-visible lessons.

MACHINE = "mch_transcription_test"


@pytest.fixture(autouse=True)
async def seed_admin(reset_db, async_engine) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('dev-owner', '', '', true) "
                "ON CONFLICT (user_id) DO UPDATE SET is_admin = excluded.is_admin"
            )
        )
        for user in ("viewer", "granted"):
            await conn.execute(
                text(
                    "INSERT INTO wcs_user_profiles (user_id, email, display_name, "
                    "is_admin) VALUES (:u, '', '', false) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {"u": user},
            )
    await grant_role(async_engine, "dev-owner", "wcs-admin")


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        yield session


async def _seed_lesson_with_chunk(
    session: AsyncSession,
    *,
    title: str | None = "Anchor step",
    is_default_visible: bool = False,
    grant_to: str | None = "granted",
    filename: str = "lesson.txt",
) -> tuple[WcsSource, WcsTranscriptChunk]:
    transcript = WcsTranscript(
        owner_id=MACHINE,
        raw_text="some transcript text",
        source_type="plaud",
        source_filename=filename,
        drive_file_id=f"drive-{_uuid.uuid4().hex[:8]}",
    )
    session.add(transcript)
    await session.flush()
    source = WcsSource(
        owner_id=MACHINE,
        transcript_id=transcript.id,
        title=title,
        session_date=dt.date(2024, 3, 8),
        session_type="private_lesson",
        instructors_raw=["Kyle"],
        students_raw=["Sarah"],
        organization="",
        is_default_visible=is_default_visible,
    )
    session.add(source)
    await session.flush()
    if grant_to:
        session.add(
            WcsSourceGrant(
                user_id=grant_to, source_id=source.id, granted_by="dev-owner"
            )
        )
    chunk = WcsTranscriptChunk(
        chunk_id=f"{transcript.id}:0",
        embedding_model="text-embedding-3-small",
        chunking_version=1,
        transcript_id=transcript.id,
        owner_id=MACHINE,
        chunk_index=0,
        start_offset=0,
        end_offset=20,
        text="some transcript text",
        embedding=[0.0] * 1536,
        content_sha="x",
    )
    session.add(chunk)
    await session.commit()
    await session.refresh(source)
    await session.refresh(chunk)
    return source, chunk


async def _enrich(session, viewer_id: str, *entries: dict):
    return await enrich_citations(
        session=session,
        entries=list(entries),
        viewer_id=viewer_id,
        site_url=SITE_URL,
    )


async def test_enrich_lesson_citation(db_session) -> None:
    source, _ = await _seed_lesson_with_chunk(db_session)
    enriched, dropped = await _enrich(
        db_session, "dev-owner", {"marker": 1, "type": "lesson", "id": str(source.id)}
    )
    assert dropped == []
    (c,) = enriched
    assert c.type == "lesson"
    assert c.id == str(source.id)
    assert c.title == "Anchor step"
    assert c.session_date == dt.date(2024, 3, 8)
    assert c.session_type == "private_lesson"
    assert c.instructors == ["Kyle"]
    assert c.students == ["Sarah"]
    assert c.source_url == f"{SITE_URL}/notes/detail?id={source.id}"
    assert c.transcript_id is None


async def test_enrich_chunk_citation_links_to_its_lesson(db_session) -> None:
    source, chunk = await _seed_lesson_with_chunk(db_session)
    enriched, dropped = await _enrich(
        db_session, "dev-owner", {"marker": 2, "type": "chunk", "id": chunk.chunk_id}
    )
    assert dropped == []
    (c,) = enriched
    assert c.type == "chunk"
    assert c.id == chunk.chunk_id
    assert c.transcript_id == str(chunk.transcript_id)
    assert c.title == "Anchor step"
    assert c.source_url == f"{SITE_URL}/notes/detail?id={source.id}"


async def test_a_granted_asker_keeps_citations_of_a_granted_lesson(db_session) -> None:
    source, chunk = await _seed_lesson_with_chunk(db_session)
    enriched, dropped = await _enrich(
        db_session,
        "granted",
        {"marker": 1, "type": "lesson", "id": str(source.id)},
        {"marker": 2, "type": "chunk", "id": chunk.chunk_id},
    )
    assert dropped == []
    assert [c.marker for c in enriched] == [1, 2]


async def test_a_public_lesson_is_kept_for_any_asker(db_session) -> None:
    source, chunk = await _seed_lesson_with_chunk(
        db_session, is_default_visible=True, grant_to=None
    )
    enriched, dropped = await _enrich(
        db_session, "viewer", {"marker": 1, "type": "chunk", "id": chunk.chunk_id}
    )
    assert dropped == []
    assert enriched[0].source_url == f"{SITE_URL}/notes/detail?id={source.id}"


async def test_a_hidden_lesson_is_dropped_both_ways(db_session) -> None:
    """A lesson the asker may not see, cited whole or by a chunk, is dropped.

    Asking as the chunks' owner (the machine principal) changes nothing.
    """
    source, chunk = await _seed_lesson_with_chunk(db_session)
    for viewer in ("viewer", MACHINE):
        enriched, dropped = await _enrich(
            db_session,
            viewer,
            {"marker": 1, "type": "lesson", "id": str(source.id)},
            {"marker": 2, "type": "chunk", "id": chunk.chunk_id},
        )
        assert enriched == []
        assert dropped == [str(source.id), chunk.chunk_id]


async def test_enrich_drops_unknown_lesson_id(db_session) -> None:
    bogus = str(_uuid.uuid4())
    enriched, dropped = await _enrich(
        db_session, "dev-owner", {"marker": 1, "type": "lesson", "id": bogus}
    )
    assert enriched == []
    assert dropped == [bogus]


async def test_enrich_drops_malformed_uuid(db_session) -> None:
    enriched, dropped = await _enrich(
        db_session, "dev-owner", {"marker": 1, "type": "lesson", "id": "not-a-uuid"}
    )
    assert enriched == []
    assert dropped == ["not-a-uuid"]


async def test_enrich_drops_malformed_chunk_id(db_session) -> None:
    enriched, dropped = await _enrich(
        db_session, "dev-owner", {"marker": 2, "type": "chunk", "id": "not-a-chunk-id"}
    )
    assert enriched == []
    assert dropped == ["not-a-chunk-id"]


async def test_a_chunk_whose_transcript_has_no_lesson_is_dropped(db_session) -> None:
    """With no lesson there is nothing to decide visibility by, or to link to."""
    transcript = WcsTranscript(
        owner_id=MACHINE,
        raw_text="orphan transcript",
        source_type="plaud",
        source_filename="orphan.txt",
        drive_file_id="d-orphan",
    )
    db_session.add(transcript)
    await db_session.flush()
    chunk = WcsTranscriptChunk(
        chunk_id=f"{transcript.id}:0",
        embedding_model="text-embedding-3-small",
        chunking_version=1,
        transcript_id=transcript.id,
        owner_id=MACHINE,
        chunk_index=0,
        start_offset=0,
        end_offset=10,
        text="orphan",
        embedding=[0.0] * 1536,
        content_sha="x",
    )
    db_session.add(chunk)
    await db_session.commit()

    enriched, dropped = await _enrich(
        db_session, "dev-owner", {"marker": 1, "type": "chunk", "id": chunk.chunk_id}
    )
    assert enriched == []
    assert dropped == [chunk.chunk_id]


async def test_a_lesson_with_no_title_is_named_by_its_file(db_session) -> None:
    source, _ = await _seed_lesson_with_chunk(
        db_session, title=None, filename="2024-03-08.txt"
    )
    enriched, _ = await _enrich(
        db_session, "dev-owner", {"marker": 1, "type": "lesson", "id": str(source.id)}
    )
    assert enriched[0].title == "2024-03-08.txt"


async def test_enrich_mixed_entries_some_drop_some_keep(db_session) -> None:
    source, chunk = await _seed_lesson_with_chunk(db_session)
    bogus_id = str(_uuid.uuid4())
    enriched, dropped = await _enrich(
        db_session,
        "dev-owner",
        {"marker": 1, "type": "lesson", "id": str(source.id)},
        {"marker": 2, "type": "lesson", "id": bogus_id},
        {"marker": 3, "type": "chunk", "id": chunk.chunk_id},
    )
    assert len(enriched) == 2
    assert dropped == [bogus_id]
    assert {c.marker for c in enriched} == {1, 3}
