"""Page documents: stored whole, served filtered, removed when gone (MINI-96).

wiki-curator-cog posts one document per entity page. The API never
recomputes a page; what these pin is that it gives each reader the evidence
whose source they can see — by the source's visibility now, not when the
page was built — and that the admin sees all of it, or the public view on
request.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from identity.store.models import Principal, PrincipalRole
from identity.types import VerifiedSubject
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import auth as auth_mod
from kaianolevine_api.main import app
from kaianolevine_api.models import (
    WcsSource,
    WcsSourceGrant,
    WcsTranscript,
    WcsUserProfile,
)
from kaianolevine_api.schemas import WcsWikiPagesWrite
from kaianolevine_api.services import wcs_wiki_pages as pages_svc

ISSUER = "https://clerk.kaianolevine.com"
READER = "page-reader"


async def _source(session: AsyncSession, *, public: bool) -> uuid.UUID:
    transcript = WcsTranscript(
        owner_id="dev-owner",
        raw_text="x",
        source_type="plaud",
        source_filename="f.txt",
        drive_file_id=f"d-{uuid.uuid4().hex[:8]}",
    )
    session.add(transcript)
    await session.flush()
    source = WcsSource(
        owner_id="dev-owner",
        transcript_id=transcript.id,
        is_default_visible=public,
        visibility="public" if public else "private",
    )
    session.add(source)
    await session.commit()
    return source.id


def _cite(source: uuid.UUID) -> dict[str, Any]:
    # Stored as public either way: the read must go by the source as it is now.
    return {
        "source_id": str(source),
        "source_slug": f"slug-{source}",
        "session_date": "2026-10-01",
        "public": True,
    }


def _page(slug: str, sources: list[uuid.UUID], **over: Any) -> dict[str, Any]:
    page = {
        "slug": slug,
        "name": slug.title(),
        "kind": "concept",
        "aliases": [f"{slug} alias"],
        "definition": f"About {slug}.",
        "by_teacher": [
            {
                "name": "Kaiano",
                "statements": [
                    {"kind": "attribution", "text": f"from {s}", "citation": _cite(s)}
                    for s in sources
                ],
            }
        ],
        "sources": [_cite(s) for s in sources],
        "evidence_sources": [str(s) for s in sources],
    }
    page.update(over)
    return page


@pytest.fixture
async def sources(client, db_session: AsyncSession) -> dict[str, uuid.UUID]:
    return {
        "public": await _source(db_session, public=True),
        "private": await _source(db_session, public=False),
        "granted": await _source(db_session, public=False),
    }


@pytest.fixture
async def reader_client(
    client, db_session: AsyncSession, sources
) -> AsyncIterator[httpx.AsyncClient]:
    """A human with the default reader role and a grant on one private source."""
    principal = Principal(kind="human", issuer=ISSUER, subject=READER, display_name="r")
    db_session.add(principal)
    await db_session.flush()
    db_session.add(
        PrincipalRole(principal_id=principal.id, role_name="wcs-reader", granted_by="t")
    )
    db_session.add(WcsUserProfile(user_id=READER, is_admin=False))
    await db_session.flush()
    db_session.add(
        WcsSourceGrant(user_id=READER, source_id=sources["granted"], granted_by="t")
    )
    await db_session.commit()
    original = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(
        return_value=VerifiedSubject(issuer=ISSUER, subject=READER, kind="human")
    )
    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer r"},
        ) as c:
            yield c
    auth_mod.verify_bearer = original


async def _put(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post("/v1/wcs/wiki/pages", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _store(session: AsyncSession, body: dict[str, Any]) -> None:
    """Write pages directly: with reader_client active, every client is the reader."""
    await pages_svc.put_pages(session, WcsWikiPagesWrite.model_validate(body))


def _texts(page: dict[str, Any]) -> list[str]:
    return [s["text"] for t in page["by_teacher"] for s in t["statements"]]


async def test_a_page_comes_back_whole_to_the_admin(client, sources) -> None:
    ids = [sources["public"], sources["private"]]
    assert await _put(client, {"pages": [_page("anchor", ids)]}) == {
        "written": 1,
        "removed": 0,
        "total": 1,
    }
    resp = await client.get("/v1/wcs/wiki/admin/pages/anchor")
    assert resp.status_code == 200, resp.text
    page = resp.json()["data"]
    assert _texts(page) == [f"from {s}" for s in ids]
    # Stored as public; the private source is marked as it is now.
    assert {c["source_id"]: c["public"] for c in page["sources"]} == {
        str(sources["public"]): True,
        str(sources["private"]): False,
    }


async def test_as_public_shows_the_admin_what_the_public_sees(client, sources) -> None:
    ids = [sources["public"], sources["private"], sources["granted"]]
    await _put(client, {"pages": [_page("anchor", ids)]})
    resp = await client.get("/v1/wcs/wiki/admin/pages/anchor", params={"as": "public"})
    assert _texts(resp.json()["data"]) == [f"from {sources['public']}"]


async def test_a_reader_gets_public_and_granted_evidence_only(
    reader_client, sources, db_session: AsyncSession
) -> None:
    ids = [sources["public"], sources["private"], sources["granted"]]
    await _store(db_session, {"pages": [_page("anchor", ids)]})
    resp = await reader_client.get("/v1/wcs/wiki/pages/anchor")
    assert resp.status_code == 200, resp.text
    page = resp.json()["data"]
    assert _texts(page) == [f"from {sources['public']}", f"from {sources['granted']}"]
    assert str(sources["private"]) not in resp.text


def _two_pages(sources: dict[str, uuid.UUID]) -> dict[str, Any]:
    return {
        "pages": [
            _page("secret", [sources["private"]]),
            _page("open", [sources["public"]]),
        ]
    }


async def test_a_page_of_hidden_evidence_does_not_exist_for_a_reader(
    reader_client, sources, db_session: AsyncSession
) -> None:
    await _store(db_session, _two_pages(sources))
    assert (await reader_client.get("/v1/wcs/wiki/pages/secret")).status_code == 404
    listed = (await reader_client.get("/v1/wcs/wiki/pages")).json()["data"]
    assert [p["slug"] for p in listed] == ["open"]


async def test_the_admin_index_lists_every_page(client, sources) -> None:
    await _put(client, _two_pages(sources))
    admin = (await client.get("/v1/wcs/wiki/admin/pages")).json()["data"]
    assert [p["slug"] for p in admin] == ["open", "secret"]
    assert admin[0]["aliases"] == ["open alias"]
    assert admin[0]["definition"] == "About open."


async def test_a_source_made_private_later_disappears_from_its_pages(
    reader_client, sources, db_session: AsyncSession
) -> None:
    await _store(db_session, {"pages": [_page("anchor", [sources["public"]])]})
    assert (await reader_client.get("/v1/wcs/wiki/pages/anchor")).status_code == 200
    source = await db_session.get(WcsSource, sources["public"])
    assert source is not None
    source.is_default_visible = False
    await db_session.commit()
    assert (await reader_client.get("/v1/wcs/wiki/pages/anchor")).status_code == 404


async def test_a_rewrite_replaces_and_a_removal_deletes(client, sources) -> None:
    await _put(client, {"pages": [_page("anchor", [sources["public"]])]})
    await _put(client, {"pages": [_page("folded", [sources["public"]])]})
    again = _page("anchor", [sources["public"]], definition="Rewritten.")
    assert await _put(client, {"pages": [again], "remove": ["folded", "anchor"]}) == {
        "written": 1,
        "removed": 1,
        "total": 1,
    }
    page = (await client.get("/v1/wcs/wiki/admin/pages/anchor")).json()["data"]
    assert page["definition"] == "Rewritten."
    assert (await client.get("/v1/wcs/wiki/admin/pages/folded")).status_code == 404


async def test_resending_a_write_changes_nothing(client, sources) -> None:
    body = {"pages": [_page("anchor", [sources["public"]])]}
    await _put(client, body)
    first = (await client.get("/v1/wcs/wiki/admin/pages/anchor")).json()["data"]
    await _put(client, body)
    assert (await client.get("/v1/wcs/wiki/admin/pages/anchor")).json()["data"] == first


async def test_reading_the_wiki_does_not_grant_writing_it(
    reader_client, sources
) -> None:
    resp = await reader_client.post(
        "/v1/wcs/wiki/pages", json={"pages": [_page("x", [sources["public"]])]}
    )
    assert resp.status_code == 403
    assert (await reader_client.get("/v1/wcs/wiki/admin/pages/x")).status_code == 403


# ── Folded slugs (MINI-100) ──


async def test_a_folded_slug_is_served_the_page_it_folded_into(client, sources) -> None:
    folded = _page("anchor", [sources["public"]], folded=["stationary-anchor"])
    await _put(client, {"pages": [folded]})

    for path in ("/v1/wcs/wiki/admin/pages", "/v1/wcs/wiki/pages"):
        resp = await client.get(f"{path}/stationary-anchor")
        assert resp.status_code == 200, resp.text
        page = resp.json()["data"]
        assert page["slug"] == "anchor"
        assert page["requested_slug"] == "stationary-anchor"


async def test_a_page_asked_for_by_its_own_slug_names_no_request(
    client, sources
) -> None:
    folded = _page("anchor", [sources["public"]], folded=["stationary-anchor"])
    await _put(client, {"pages": [folded]})
    page = (await client.get("/v1/wcs/wiki/admin/pages/anchor")).json()["data"]
    assert page["requested_slug"] is None
    assert page["folded"] == ["stationary-anchor"]


async def test_a_folded_slug_keeps_the_readers_visibility(
    reader_client, sources, db_session: AsyncSession
) -> None:
    hidden = _page("secret", [sources["private"]], folded=["secret-facet"])
    await _store(db_session, {"pages": [hidden]})
    resp = await reader_client.get("/v1/wcs/wiki/pages/secret-facet")
    assert resp.status_code == 404


async def test_a_slug_folded_nowhere_is_still_not_found(client, sources) -> None:
    await _put(client, {"pages": [_page("anchor", [sources["public"]])]})
    assert (await client.get("/v1/wcs/wiki/admin/pages/nowhere")).status_code == 404


async def test_a_requested_slug_sent_in_a_write_is_not_stored(client, sources) -> None:
    sent = _page("anchor", [sources["public"]], requested_slug="stray")
    await _put(client, {"pages": [sent]})
    page = (await client.get("/v1/wcs/wiki/admin/pages/anchor")).json()["data"]
    assert page["requested_slug"] is None
