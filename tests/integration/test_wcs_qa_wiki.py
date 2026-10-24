"""The Q&A answers from the wiki's pages.

Pinned here: pages are embedded and kept current like notes; search_wiki and
get_wiki_page give an asker only the pages the /wiki routes would; a page is
found by slug, display name or other name; a cited page is enriched with its
/wiki link, or dropped when the asker may not see it; and a page write that
changed something asks for an embeddings refresh.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.agents.wcs_qa.citations import enrich_citations
from kaianolevine_api.models import WcsUserProfile, WcsWikiPageEmbedding
from kaianolevine_api.retrieval.wcs import auto_refresh, convergence, wiki_pages
from kaianolevine_api.retrieval.wcs.chunker import Chunk
from kaianolevine_api.retrieval.wcs.convergence import refresh_embeddings
from kaianolevine_api.retrieval.wcs.schemas import ToolError
from kaianolevine_api.schemas import WcsWikiPagesWrite
from kaianolevine_api.services import wcs_wiki_pages as pages_svc
from tests.integration.conftest import grant_role

from .test_wcs_qa import StubEmbedder
from .test_wcs_wiki_pages import _page, _source

SITE = "https://wcs.example"
ADMIN = "wiki-admin"
READER = "wiki-reader"


@pytest.fixture(autouse=True)
def one_chunk_per_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seeded sources have transcripts; chunk them without tiktoken."""
    monkeypatch.setattr(
        convergence,
        "chunk_transcript",
        lambda t: [Chunk(chunk_index=0, start_offset=0, end_offset=len(t), text=t)],
    )


@pytest.fixture
async def seeded(db_session: AsyncSession, async_engine) -> dict[str, uuid.UUID]:
    """A public page, a private one, and one with no evidence; two askers."""
    public = await _source(db_session, public=True)
    private = await _source(db_session, public=False)
    db_session.add(WcsUserProfile(user_id=ADMIN, is_admin=True))
    db_session.add(WcsUserProfile(user_id=READER, is_admin=False))
    await db_session.commit()
    await grant_role(async_engine, ADMIN, "wcs-admin")
    await pages_svc.put_pages(
        db_session,
        WcsWikiPagesWrite.model_validate(
            {
                "pages": [
                    _page(
                        "anchor-step",
                        [public],
                        name="anchor step",
                        aliases=["anchor"],
                        definition="The last two counts of a pattern [[sources/x|lesson]].",
                        in_practice="Land on the ball of the foot on six.",
                    ),
                    _page("secret-drill", [private], name="secret drill"),
                    _page("frame", [], name="frame", evidence_sources=[]),
                ]
            }
        ),
    )
    return {"public": public, "private": private}


async def _embed(session: AsyncSession, embedder: StubEmbedder) -> Any:
    return await refresh_embeddings(
        session=session,
        embedder=embedder,
        embedding_model="m",
        chunking_version=1,
        page_flattener_version=1,
    )


def test_plain_text_keeps_what_a_reader_sees() -> None:
    text = "See [[concepts/frame]] and [[x|the anchor]] [[source:00000000-0000-0000-0000-000000000000]]."
    assert wiki_pages.plain_text(text) == "See frame and the anchor ."


def test_a_page_is_embedded_as_its_own_text() -> None:
    flat = wiki_pages.flatten_page(
        name="anchor step",
        kind="technique",
        aliases=["anchor"],
        definition="The end of a pattern.",
        in_practice="Land on the ball [[sources/x|lesson]].",
        details=["triple: three steps"],
    )
    assert flat == (
        "anchor step (technique)\n\nAlso called: anchor\n\nThe end of a pattern."
        "\n\nLand on the ball lesson.\n\n- triple: three steps"
    )


async def test_pages_are_embedded_once_and_again_when_they_change(
    db_session: AsyncSession, seeded
) -> None:
    stub = StubEmbedder()
    first = await _embed(db_session, stub)
    assert (first.pages_total, first.pages_embedded) == (3, 3)
    again = await _embed(db_session, stub)
    assert (again.pages_embedded, again.pages_skipped) == (0, 3)

    await pages_svc.put_pages(
        db_session,
        WcsWikiPagesWrite.model_validate(
            {
                "pages": [_page("frame", [], name="frame", definition="Changed.")],
                "remove": ["secret-drill"],
            }
        ),
    )
    third = await _embed(db_session, stub)
    assert (third.pages_total, third.pages_embedded) == (2, 1)
    count = await db_session.scalar(
        select(func.count()).select_from(WcsWikiPageEmbedding)
    )
    assert count == 2


async def test_search_gives_each_asker_the_pages_they_may_see(
    db_session: AsyncSession, seeded
) -> None:
    stub = StubEmbedder()
    await _embed(db_session, stub)
    kwargs = {
        "session": db_session,
        "embedder": stub,
        "embedding_model": "m",
        "page_flattener_version": 1,
        "query": "anchor",
        "k": 10,
    }
    admin = {h.slug for h in await wiki_pages.search_wiki(viewer_id=ADMIN, **kwargs)}
    reader = {h.slug for h in await wiki_pages.search_wiki(viewer_id=READER, **kwargs)}
    assert admin == {"anchor-step", "secret-drill", "frame"}
    assert reader == {"anchor-step", "frame"}


@pytest.mark.parametrize("key", ["anchor-step", "anchor step", "Anchor Step", "anchor"])
async def test_a_page_is_found_by_slug_name_or_other_name(
    db_session: AsyncSession, seeded, key: str
) -> None:
    page = await wiki_pages.get_wiki_page(
        session=db_session, viewer_id=READER, site_url=SITE, name=key
    )
    assert page.slug == "anchor-step"
    assert page.definition == "The last two counts of a pattern lesson."
    assert page.source_url == f"{SITE}/wiki/page?slug=anchor-step"


async def test_a_hidden_page_is_not_found(db_session: AsyncSession, seeded) -> None:
    with pytest.raises(ToolError):
        await wiki_pages.get_wiki_page(
            session=db_session, viewer_id=READER, site_url=SITE, name="secret-drill"
        )
    page = await wiki_pages.get_wiki_page(
        session=db_session, viewer_id=ADMIN, site_url=SITE, name="secret-drill"
    )
    assert page.slug == "secret-drill"


async def test_a_cited_page_carries_its_wiki_link(
    db_session: AsyncSession, seeded
) -> None:
    enriched, dropped = await enrich_citations(
        session=db_session,
        entries=[
            {"marker": 1, "type": "page", "id": "anchor"},
            {"marker": 2, "type": "page", "id": "secret-drill"},
        ],
        viewer_id=READER,
        site_url=SITE,
    )
    assert dropped == ["secret-drill"]
    assert [(c.type, c.id, c.title, c.kind, c.source_url) for c in enriched] == [
        (
            "page",
            "anchor-step",
            "anchor step",
            "concept",
            f"{SITE}/wiki/page?slug=anchor-step",
        )
    ]


async def test_a_page_write_that_changes_something_asks_for_a_refresh(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(auto_refresh, "request_refresh", lambda _s: calls.append(1))
    body = {"pages": [_page("frame", [], name="frame", evidence_sources=[])]}
    first = await client.post("/v1/wcs/wiki/pages", json=body)
    assert first.status_code == 200, first.text
    assert calls == [1]
    same = await client.post("/v1/wcs/wiki/pages", json=body)
    assert same.status_code == 200
    assert calls == [1]


async def test_the_agent_can_read_a_page(db_session: AsyncSession, seeded) -> None:
    from kaianolevine_api.agents.wcs_qa import loop
    from kaianolevine_api.agents.wcs_qa.config import AgentConfig

    names = {t["name"] for t in loop._tool_definitions()}
    assert {"search_wiki", "get_wiki_page"} <= names
    config = AgentConfig(
        model="m",
        max_tool_calls=5,
        max_input_tokens=1000,
        max_output_tokens=100,
        embedding_model="m",
        chunking_version=1,
        site_url=SITE,
    )
    out = await loop._dispatch_tool(
        name="get_wiki_page",
        inputs={"name": "anchor"},
        session=db_session,
        embedder=StubEmbedder(),
        viewer_id=READER,
        config=config,
    )
    assert out["slug"] == "anchor-step"
    assert out["requested"] == "anchor"
