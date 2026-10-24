"""Tool-level retrieval tests with fixture data.

Covers the lesson tools (search_transcripts, get_transcript_window) end to end
against the Postgres test DB. Uses a keyword-based stub embedder so cosine
ranking is meaningful and deterministic.

Lessons are ``WcsSource`` rows, and their transcripts and chunks are owned by
the transcription cog's machine principal, as in production: who may see a
lesson is the source's visibility, never the chunk's owner. Three askers:
``dev-owner`` holds the whole corpus (wcs-admin), ``granted`` has a grant on
one private lesson, and ``viewer`` sees only default-visible lessons.
"""

from __future__ import annotations

import datetime as dt
import uuid as _uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.models import WcsSource, WcsSourceGrant, WcsTranscript
from kaianolevine_api.retrieval.wcs.convergence import refresh_embeddings
from kaianolevine_api.retrieval.wcs.schemas import (
    ToolError,
    TranscriptFilters,
)
from kaianolevine_api.retrieval.wcs.tools import (
    get_transcript_window,
    search_transcripts,
)
from tests.integration.conftest import grant_role

EMBEDDING_MODEL = "text-embedding-3-small"
#: Who owns every transcript and chunk: the transcription cog, not a person.
MACHINE = "mch_transcription_test"
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
                "VALUES ('dev-owner', '', '', true) "
                "ON CONFLICT (user_id) DO UPDATE SET is_admin = excluded.is_admin"
            )
        )
        # Askers with no corpus role: one with a grant, one without.
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
async def session_maker(async_engine):
    return async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def db_session(session_maker) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session


@pytest.fixture
def stub_embedder() -> KeywordEmbedder:
    return KeywordEmbedder()


async def _seed_lesson(
    session: AsyncSession,
    *,
    raw_text: str,
    source_filename: str,
    title: str | None,
    instructors: list[str],
    students: list[str],
    session_date: dt.date | None,
    session_type: str = "private_lesson",
    organization: str = "",
    is_default_visible: bool = False,
) -> tuple[WcsTranscript, WcsSource]:
    t = WcsTranscript(
        owner_id=MACHINE,
        raw_text=raw_text,
        source_type="plaud",
        source_filename=source_filename,
        drive_file_id=f"drive-{_uuid.uuid4().hex[:8]}",
    )
    session.add(t)
    await session.flush()
    s = WcsSource(
        owner_id=MACHINE,
        transcript_id=t.id,
        title=title,
        session_date=session_date,
        session_type=session_type,
        instructors_raw=instructors,
        students_raw=students,
        organization=organization,
        is_default_visible=is_default_visible,
    )
    session.add(s)
    await session.commit()
    await session.refresh(t)
    await session.refresh(s)
    return t, s


async def _populate_corpus(session: AsyncSession, embedder: KeywordEmbedder) -> dict:
    """Seed three lessons and run convergence. Returns id mapping.

    The anchor lesson is public, the frame lesson is private and granted to
    ``granted``, the competition lesson is private and granted to no one.
    """
    t_anchor, s_anchor = await _seed_lesson(
        session,
        raw_text=(
            "Worked with Sarah on anchor step timing. The anchor lands on counts "
            "5 and 6. We did several drill repetitions."
        ),
        source_filename="2024-03-08-anchor.txt",
        title="Anchor step deep dive",
        instructors=["Kyle"],
        students=["Sarah"],
        session_date=dt.date(2024, 3, 8),
        is_default_visible=True,
    )
    t_frame, s_frame = await _seed_lesson(
        session,
        raw_text="Frame and axis lesson. Worked on frame stability with an anchor.",
        source_filename="2024-04-12-frame.txt",
        title="Frame and axis",
        instructors=["Kaiano"],
        students=["Sarah"],
        session_date=dt.date(2024, 4, 12),
        session_type="group_class",
        organization="Swing Club",
    )
    session.add(
        WcsSourceGrant(user_id="granted", source_id=s_frame.id, granted_by="dev-owner")
    )
    await session.commit()
    t_competition, s_competition = await _seed_lesson(
        session,
        raw_text="Open Jack and Jill competition prelims went well. Anchor held.",
        source_filename="2024-05-15-competition.txt",
        title=None,
        instructors=["Kyle"],
        students=["Sarah"],
        session_date=dt.date(2024, 5, 15),
    )

    summary = await refresh_embeddings(
        session=session,
        embedder=embedder,
        embedding_model=EMBEDDING_MODEL,
        chunking_version=CHUNKING_VERSION,
    )
    assert summary.transcripts_embedded == 3

    return {
        "s_anchor": s_anchor.id,
        "s_frame": s_frame.id,
        "s_competition": s_competition.id,
        "t_anchor": t_anchor.id,
        "t_frame": t_frame.id,
        "t_competition": t_competition.id,
    }


async def _search(session, embedder, viewer_id: str, query: str, **kw):
    return await search_transcripts(
        session=session,
        embedder=embedder,
        viewer_id=viewer_id,
        embedding_model=EMBEDDING_MODEL,
        chunking_version=CHUNKING_VERSION,
        query=query,
        **kw,
    )


async def _window(session, viewer_id: str, chunk_id: str, **kw):
    return await get_transcript_window(
        session=session,
        viewer_id=viewer_id,
        site_url=SITE_URL,
        embedding_model=EMBEDDING_MODEL,
        chunking_version=CHUNKING_VERSION,
        chunk_id=chunk_id,
        **kw,
    )


# ── search_transcripts: who sees which lessons ────────────────────────────────


async def test_the_whole_corpus_sees_every_lesson(db_session, stub_embedder) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await _search(db_session, stub_embedder, "dev-owner", "anchor")
    assert {h.source_id for h in hits} == {
        str(ids["s_anchor"]),
        str(ids["s_frame"]),
        str(ids["s_competition"]),
    }
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


async def test_a_granted_asker_sees_public_and_granted_lessons(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await _search(db_session, stub_embedder, "granted", "anchor")
    assert {h.source_id for h in hits} == {str(ids["s_anchor"]), str(ids["s_frame"])}


async def test_an_asker_with_no_grants_sees_only_public_lessons(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await _search(db_session, stub_embedder, "viewer", "anchor")
    assert {h.source_id for h in hits} == {str(ids["s_anchor"])}


async def test_the_chunks_owner_never_decides_visibility(
    db_session, stub_embedder
) -> None:
    """Every chunk is the machine principal's; asking as it adds nothing."""
    await _populate_corpus(db_session, stub_embedder)
    hits = await _search(db_session, stub_embedder, MACHINE, "competition")
    assert all(h.transcript_title != "2024-05-15-competition.txt" for h in hits)


# ── search_transcripts: what a hit carries ────────────────────────────────────


async def test_a_hit_carries_its_lessons_metadata(db_session, stub_embedder) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await _search(db_session, stub_embedder, "dev-owner", "frame axis")
    top = hits[0]
    assert top.source_id == str(ids["s_frame"])
    assert top.chunk_id.startswith(top.transcript_id)
    assert ":" in top.chunk_id
    assert top.transcript_title == "Frame and axis"
    assert top.session_date == dt.date(2024, 4, 12)
    assert top.session_type == "group_class"
    assert top.instructors == ["Kaiano"]
    assert top.students == ["Sarah"]
    assert top.organization == "Swing Club"
    assert top.start_offset >= 0
    assert not hasattr(top, "source_url")


async def test_a_lesson_with_no_title_is_named_by_its_file(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await _search(db_session, stub_embedder, "dev-owner", "competition")
    top = hits[0]
    assert top.source_id == str(ids["s_competition"])
    assert top.transcript_title == "2024-05-15-competition.txt"


async def test_search_respects_and_clamps_k(db_session, stub_embedder) -> None:
    await _populate_corpus(db_session, stub_embedder)
    assert (
        len(await _search(db_session, stub_embedder, "dev-owner", "anchor", k=1)) == 1
    )
    hits = await _search(db_session, stub_embedder, "dev-owner", "anchor", k=999)
    assert len(hits) == 3  # one chunk per lesson; the corpus is under K_MAX


async def test_search_filters_by_the_lessons_date_range(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await _search(
        db_session,
        stub_embedder,
        "dev-owner",
        "anchor",
        filters=TranscriptFilters(
            date_from=dt.date(2024, 4, 1), date_to=dt.date(2024, 4, 30)
        ),
    )
    assert [h.source_id for h in hits] == [str(ids["s_frame"])]


async def test_search_filters_by_the_lessons_instructors(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    hits = await _search(
        db_session,
        stub_embedder,
        "dev-owner",
        "lesson",
        filters=TranscriptFilters(instructors=["Kaiano"]),
    )
    assert [h.source_id for h in hits] == [str(ids["s_frame"])]


# ── get_transcript_window ─────────────────────────────────────────────────────


async def test_a_window_carries_its_lesson_and_links_to_it(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    window = await _window(
        db_session, "viewer", f"{ids['t_anchor']}:0", before=0, after=0
    )
    assert window.transcript_id == str(ids["t_anchor"])
    assert window.source_id == str(ids["s_anchor"])
    assert window.transcript_title == "Anchor step deep dive"
    assert window.session_date == dt.date(2024, 3, 8)
    assert window.instructors == ["Kyle"]
    assert len(window.chunks) == 1
    assert window.chunks[0].chunk_index == 0
    # The lesson's page on the site, which reads a source id.
    assert window.source_url == f"{SITE_URL}/notes/detail?id={ids['s_anchor']}"


async def test_a_granted_asker_reads_a_granted_lesson(
    db_session, stub_embedder
) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    window = await _window(db_session, "granted", f"{ids['t_frame']}:0")
    assert window.source_id == str(ids["s_frame"])


async def test_a_hidden_lessons_window_is_not_found(db_session, stub_embedder) -> None:
    """Existence is masked: a lesson the asker may not see is not_found."""
    ids = await _populate_corpus(db_session, stub_embedder)
    for viewer, transcript in (
        ("viewer", ids["t_frame"]),
        ("granted", ids["t_competition"]),
        (MACHINE, ids["t_competition"]),
    ):
        with pytest.raises(ToolError) as exc_info:
            await _window(db_session, viewer, f"{transcript}:0")
        assert exc_info.value.code == "not_found"


async def test_the_whole_corpus_reads_any_lesson(db_session, stub_embedder) -> None:
    ids = await _populate_corpus(db_session, stub_embedder)
    window = await _window(db_session, "dev-owner", f"{ids['t_competition']}:0")
    assert window.source_id == str(ids["s_competition"])
    assert window.transcript_title == "2024-05-15-competition.txt"


async def test_get_transcript_window_unknown_chunk_raises(db_session) -> None:
    with pytest.raises(ToolError) as exc_info:
        await _window(db_session, "dev-owner", f"{_uuid.uuid4()}:0")
    assert exc_info.value.code == "not_found"


async def test_get_transcript_window_malformed_chunk_id_raises(db_session) -> None:
    with pytest.raises(ToolError) as exc_info:
        await _window(db_session, "dev-owner", "not-a-valid-id")
    assert exc_info.value.code == "invalid_input"


async def test_get_transcript_window_negative_before_raises(db_session) -> None:
    with pytest.raises(ToolError) as exc_info:
        await _window(db_session, "dev-owner", f"{_uuid.uuid4()}:0", before=-1)
    assert exc_info.value.code == "invalid_input"
