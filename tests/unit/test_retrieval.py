"""Tool-level retrieval tests with fixture data.

Covers the four retrieval tools (search_notes, search_transcripts, get_note,
get_transcript_window) end-to-end against the SQLite test DB. Uses a keyword-
based stub embedder so cosine ranking is meaningful and deterministic.
"""

from __future__ import annotations

import datetime as dt
import uuid as _uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.models import WcsNote, WcsTranscript
from kaianolevine_api.retrieval.wcs.convergence import refresh_embeddings
from kaianolevine_api.retrieval.wcs.schemas import (
    NoteFilters,
    ToolError,
    TranscriptFilters,
)
from kaianolevine_api.retrieval.wcs.tools import (
    get_note,
    get_transcript_window,
    search_notes,
    search_transcripts,
)

EMBEDDING_MODEL = "text-embedding-3-small"
FLATTENER_VERSION = 1
CHUNKING_VERSION = 1
SITE_URL = "https://wcs.example.com"

KEYWORDS = [
    "anchor",
    "frame",
    "axis",
    "drill",
    "competition",
    "sarah",
    "kyle",
    "kaiano",
]


class KeywordEmbedder:
    """Distinguishable, deterministic 1536-dim vectors based on keyword presence."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str) -> list[float]:
        v = [0.0] * 1536
        lo = text.lower()
        for i, kw in enumerate(KEYWORDS):
            if kw in lo:
                v[i] = 1.0
        v[1535] = 0.01  # avoid all-zero vector
        return v


@pytest.fixture(autouse=True)
async def seed_dev_owner_admin(reset_db, async_engine) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('dev-owner', '', '', 1) "
                "ON CONFLICT (user_id) DO UPDATE SET is_admin = excluded.is_admin"
            )
        )
        # Non-admin user used for visibility tests.
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('viewer', '', '', 0) "
                "ON CONFLICT (user_id) DO NOTHING"
            )
        )


@pytest.fixture
async def session_maker(async_engine):
    return async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def db_session(session_maker) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session


@pytest.fixture
def stub_embedder() -> KeywordEmbedder:
    return KeywordEmbedder()


async def _seed_transcript(
    session: AsyncSession,
    *,
    owner_id: str = "dev-owner",
    raw_text: str,
    source_filename: str,
) -> WcsTranscript:
    t = WcsTranscript(
        owner_id=owner_id,
        raw_text=raw_text,
        source_type="plaud",
        source_filename=source_filename,
        drive_file_id=f"drive-{_uuid.uuid4().hex[:8]}",
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return t


async def _seed_note(
    session: AsyncSession,
    *,
    transcript_id: _uuid.UUID,
    title: str,
    instructors: list[str],
    students: list[str],
    organization: str = "",
    session_type: str = "private_lesson",
    session_date: dt.date | None = None,
    summary: str = "",
    key_concepts: list[str] | None = None,
    is_default_visible: bool = False,
    owner_id: str = "dev-owner",
) -> WcsNote:
    n = WcsNote(
        owner_id=owner_id,
        transcript_id=transcript_id,
        title=title,
        session_date=session_date,
        session_type=session_type,
        visibility="private",
        model="claude-sonnet-4-6",
        provider="anthropic",
        notes_json={"summary": summary, "key_concepts": key_concepts or []},
        instructors=instructors,
        students=students,
        organization=organization,
        is_default_visible=is_default_visible,
    )
    session.add(n)
    await session.commit()
    await session.refresh(n)
    return n


async def _populate_corpus(session: AsyncSession, embedder: KeywordEmbedder) -> dict:
    """Seed a small fixture corpus and run convergence. Returns id mapping."""
    t_anchor = await _seed_transcript(
        session,
        raw_text=(
            "Worked with Sarah on anchor step timing. The anchor lands on counts "
            "5 and 6. We did several drill repetitions."
        ),
        source_filename="2024-03-08-anchor.txt",
    )
    n_anchor = await _seed_note(
        session,
        transcript_id=t_anchor.id,
        title="Anchor step deep dive",
        instructors=["Kyle"],
        students=["Sarah"],
        session_date=dt.date(2024, 3, 8),
        summary="Deep work on anchor step timing with Sarah.",
        key_concepts=["anchor"],
    )

    t_frame = await _seed_transcript(
        session,
        raw_text="Frame and axis lesson. Worked on frame stability.",
        source_filename="2024-04-12-frame.txt",
    )
    n_frame = await _seed_note(
        session,
        transcript_id=t_frame.id,
        title="Frame and axis",
        instructors=["Kaiano"],
        students=["Sarah"],
        session_date=dt.date(2024, 4, 12),
        summary="Frame stability and axis work.",
        key_concepts=["frame", "axis"],
    )

    t_competition = await _seed_transcript(
        session,
        raw_text="Open Jack and Jill competition prelims went well.",
        source_filename="2024-05-15-competition.txt",
    )
    n_competition = await _seed_note(
        session,
        transcript_id=t_competition.id,
        title="Competition recap",
        instructors=["Kyle"],
        students=["Sarah"],
        session_type="coaching_session",
        session_date=dt.date(2024, 5, 15),
        summary="Recap of competition prelims.",
        key_concepts=["competition"],
    )

    summary = await refresh_embeddings(
        session=session,
        embedder=embedder,
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        chunking_version=CHUNKING_VERSION,
    )
    assert summary.notes_embedded == 3
    assert summary.transcripts_embedded == 3

    return {
        "n_anchor": n_anchor.id,
        "n_frame": n_frame.id,
        "n_competition": n_competition.id,
        "t_anchor": t_anchor.id,
        "t_frame": t_frame.id,
        "t_competition": t_competition.id,
    }


# ── search_notes ──────────────────────────────────────────────────────────────


async def test_search_notes_orders_by_relevance(db_session, stub_embedder) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="anchor step",
    )
    assert len(hits) == 3
    assert hits[0].note_id == str(ids["n_anchor"])
    assert hits[0].score >= hits[1].score >= hits[2].score


async def test_search_notes_respects_k(db_session, stub_embedder) -> None:
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="anchor",
        k=1,
    )
    assert len(hits) == 1


async def test_search_notes_clamps_k_at_max(db_session, stub_embedder) -> None:
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="anchor",
        k=999,
    )
    assert len(hits) == 3  # corpus is smaller than K_MAX


async def test_search_notes_no_source_url_on_hits(db_session, stub_embedder) -> None:
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="anchor",
    )
    for hit in hits:
        assert not hasattr(hit, "source_url")


async def test_search_notes_filters_by_session_type(db_session, stub_embedder) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="anchor",
        filters=NoteFilters(session_type="coaching_session"),
    )
    assert len(hits) == 1
    assert hits[0].note_id == str(ids["n_competition"])


async def test_search_notes_filters_by_date_range(db_session, stub_embedder) -> None:
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="anchor",
        filters=NoteFilters(
            date_from=dt.date(2024, 4, 1), date_to=dt.date(2024, 4, 30)
        ),
    )
    assert len(hits) == 1
    assert hits[0].title == "Frame and axis"


async def test_search_notes_filters_by_instructors(db_session, stub_embedder) -> None:
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="lesson",
        filters=NoteFilters(instructors=["Kaiano"]),
    )
    assert len(hits) == 1
    assert hits[0].instructors == ["Kaiano"]


async def test_search_notes_visibility_blocks_non_admin(
    db_session, stub_embedder
) -> None:
    """Non-admin viewer with no grants sees no private notes."""
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="viewer",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="anchor",
    )
    assert hits == []


async def test_search_notes_visibility_default_visible_passes(
    db_session, stub_embedder, async_engine
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    async with async_engine.begin() as conn:
        await conn.execute(
            text("UPDATE wcs_notes SET is_default_visible = 1 WHERE id = :id"),
            {"id": ids["n_anchor"].hex},
        )
    hits = await search_notes(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="viewer",
        embedding_model=EMBEDDING_MODEL,
        flattener_version=FLATTENER_VERSION,
        query="anchor",
    )
    assert len(hits) == 1
    assert hits[0].note_id == str(ids["n_anchor"])


# ── search_transcripts ────────────────────────────────────────────────────────


async def test_search_transcripts_returns_chunk_hits(db_session, stub_embedder) -> None:
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_transcripts(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        owner_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        chunking_version=CHUNKING_VERSION,
        query="anchor",
    )
    assert len(hits) >= 1
    top = hits[0]
    assert top.chunk_id.startswith(top.transcript_id)
    assert ":" in top.chunk_id
    assert top.transcript_title  # populated from linked note title or filename
    assert top.start_offset >= 0


async def test_search_transcripts_owner_scoping(db_session, stub_embedder) -> None:
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_transcripts(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="other-user",
        owner_id="other-user",
        embedding_model=EMBEDDING_MODEL,
        chunking_version=CHUNKING_VERSION,
        query="anchor",
    )
    assert hits == []


async def test_search_transcripts_filters_by_instructors(
    db_session, stub_embedder
) -> None:
    await _populate_corpus(db_session, stub_embedder)
    hits = await search_transcripts(
        session=db_session,
        embedder=stub_embedder,
        viewer_id="dev-owner",
        owner_id="dev-owner",
        embedding_model=EMBEDDING_MODEL,
        chunking_version=CHUNKING_VERSION,
        query="lesson",
        filters=TranscriptFilters(instructors=["Kaiano"]),
    )
    for hit in hits:
        assert "Kaiano" in hit.instructors


# ── get_note ──────────────────────────────────────────────────────────────────


async def test_get_note_returns_full_note_with_source_url(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    note = await get_note(
        session=db_session,
        viewer_id="dev-owner",
        site_url=SITE_URL,
        note_id=ids["n_anchor"],
    )
    assert note.note_id == str(ids["n_anchor"])
    assert note.title == "Anchor step deep dive"
    assert note.source_url == f"{SITE_URL}/notes/{ids['n_anchor']}"
    assert "summary" in note.notes_json


async def test_get_note_unknown_id_raises_not_found(db_session) -> None:
    with pytest.raises(ToolError) as exc_info:
        await get_note(
            session=db_session,
            viewer_id="dev-owner",
            site_url=SITE_URL,
            note_id=_uuid.uuid4(),
        )
    assert exc_info.value.code == "not_found"


async def test_get_note_invisible_to_viewer_raises_not_found(
    db_session, stub_embedder
) -> None:
    """Existence is masked: invisible note is reported as not_found."""
    ids = await _populate_corpus(db_session, stub_embedder)
    with pytest.raises(ToolError) as exc_info:
        await get_note(
            session=db_session,
            viewer_id="viewer",
            site_url=SITE_URL,
            note_id=ids["n_anchor"],
        )
    assert exc_info.value.code == "not_found"


# ── get_transcript_window ─────────────────────────────────────────────────────


async def test_get_transcript_window_returns_chunk_with_url(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    chunk_id = f"{ids['t_anchor']}:0"
    window = await get_transcript_window(
        session=db_session,
        owner_id="dev-owner",
        site_url=SITE_URL,
        embedding_model=EMBEDDING_MODEL,
        chunking_version=CHUNKING_VERSION,
        chunk_id=chunk_id,
        before=0,
        after=0,
    )
    assert window.transcript_id == str(ids["t_anchor"])
    assert window.transcript_title == "Anchor step deep dive"
    assert len(window.chunks) == 1
    assert window.chunks[0].chunk_index == 0
    # Option (b): chunks link to the underlying note's page, not a transcript reader.
    assert window.source_url == f"{SITE_URL}/notes/{ids['n_anchor']}"


async def test_get_transcript_window_unknown_chunk_raises(db_session) -> None:
    with pytest.raises(ToolError) as exc_info:
        await get_transcript_window(
            session=db_session,
            owner_id="dev-owner",
            site_url=SITE_URL,
            embedding_model=EMBEDDING_MODEL,
            chunking_version=CHUNKING_VERSION,
            chunk_id=f"{_uuid.uuid4()}:0",
        )
    assert exc_info.value.code == "not_found"


async def test_get_transcript_window_malformed_chunk_id_raises(db_session) -> None:
    with pytest.raises(ToolError) as exc_info:
        await get_transcript_window(
            session=db_session,
            owner_id="dev-owner",
            site_url=SITE_URL,
            embedding_model=EMBEDDING_MODEL,
            chunking_version=CHUNKING_VERSION,
            chunk_id="not-a-valid-id",
        )
    assert exc_info.value.code == "invalid_input"


async def test_get_transcript_window_cross_owner_returns_not_found(
    db_session, stub_embedder
) -> None:
    """Existence is masked: chunk owned by another user is not_found."""
    ids = await _populate_corpus(db_session, stub_embedder)
    chunk_id = f"{ids['t_anchor']}:0"
    with pytest.raises(ToolError) as exc_info:
        await get_transcript_window(
            session=db_session,
            owner_id="other-user",
            site_url=SITE_URL,
            embedding_model=EMBEDDING_MODEL,
            chunking_version=CHUNKING_VERSION,
            chunk_id=chunk_id,
        )
    assert exc_info.value.code == "not_found"


async def test_get_transcript_window_negative_before_raises(db_session) -> None:
    with pytest.raises(ToolError) as exc_info:
        await get_transcript_window(
            session=db_session,
            owner_id="dev-owner",
            site_url=SITE_URL,
            embedding_model=EMBEDDING_MODEL,
            chunking_version=CHUNKING_VERSION,
            chunk_id=f"{_uuid.uuid4()}:0",
            before=-1,
        )
    assert exc_info.value.code == "invalid_input"
