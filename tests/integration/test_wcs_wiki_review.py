"""The wiki's review trail: page revisions and thin entities.

Every page write records what it created, changed or removed, with the
source whose work caused it, so /admin/wiki can show what each source did to
the wiki. The thin-entity list is the curator's to replace; the API stores
and serves it.
"""

from __future__ import annotations

import datetime as dt
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
from kaianolevine_api.models import WcsSource, WcsTranscript

REVISIONS = "/v1/wcs/wiki/admin/revisions"
THIN = "/v1/wcs/wiki/state/thin"


async def _source(session: AsyncSession, title: str) -> uuid.UUID:
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
        title=title,
        session_date=dt.date(2026, 10, 1),
        session_type="group_class",
    )
    session.add(source)
    await session.commit()
    return source.id


def _page(slug: str, **over: Any) -> dict[str, Any]:
    page = {
        "slug": slug,
        "name": slug.title(),
        "kind": "concept",
        "definition": f"About {slug}.",
    }
    page.update(over)
    return page


async def _write(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post("/v1/wcs/wiki/pages", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    resp = await client.get(path, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def source(client, db_session: AsyncSession) -> uuid.UUID:
    return await _source(db_session, "Anchors at Swingtacular")


async def test_a_write_records_what_it_created_changed_and_removed(
    client, source
) -> None:
    await _write(client, {"pages": [_page("anchor"), _page("frame"), _page("old")]})
    await _write(
        client,
        {
            "pages": [
                _page("anchor"),  # identical: no revision
                _page("frame", definition="Reworded."),
                _page("stretch"),
            ],
            "remove": ["old", "never-was"],
            "origin_source_id": str(source),
            "origin_job_id": "msg-1",
        },
    )

    listed = await _get(client, REVISIONS, source_id=str(source))
    assert listed["meta"]["total"] == 3
    by_slug = {r["slug"]: r for r in listed["data"]}
    assert {s: r["change"] for s, r in by_slug.items()} == {
        "frame": "changed",
        "stretch": "created",
        "old": "removed",
    }
    for r in listed["data"]:
        assert r["origin_source_id"] == str(source)
        assert r["origin_job_id"] == "msg-1"
        assert "document" not in r and "previous" not in r
    # A removed page is named from what it was.
    assert by_slug["old"]["name"] == "Old"
    assert by_slug["old"]["kind"] == "concept"

    async def detail(slug: str) -> dict[str, Any]:
        return (await _get(client, f"{REVISIONS}/{by_slug[slug]['id']}"))["data"]

    changed = await detail("frame")
    assert changed["previous"]["definition"] == "About frame."
    assert changed["document"]["definition"] == "Reworded."
    created = await detail("stretch")
    assert created["previous"] is None
    assert created["document"]["slug"] == "stretch"
    removed = await detail("old")
    assert removed["previous"]["definition"] == "About old."
    assert removed["document"] is None


async def test_an_identical_rewrite_records_nothing(client) -> None:
    body = {"pages": [_page("anchor")]}
    await _write(client, body)
    await _write(client, body)
    listed = await _get(client, REVISIONS, slug="anchor")
    assert [r["change"] for r in listed["data"]] == ["created"]


async def test_revisions_are_grouped_by_the_source_that_caused_them(
    client, db_session, source
) -> None:
    gone = await _source(db_session, "Deleted later")
    await _write(client, {"pages": [_page("anchor"), _page("frame")]})  # no origin
    await _write(
        client,
        {"pages": [_page("anchor", definition="New.")], "origin_source_id": str(gone)},
    )
    await db_session.delete(await db_session.get(WcsSource, gone))
    await db_session.commit()
    await _write(
        client,
        {
            "pages": [_page("anchor", definition="Newer."), _page("stretch")],
            "remove": ["frame"],
            "origin_source_id": str(source),
        },
    )

    groups = await _get(client, f"{REVISIONS}/sources")
    assert groups["meta"]["total"] == 3
    rows = groups["data"]
    # Most recently active first.
    assert [r["source_id"] for r in rows] == [str(source), str(gone), None]
    newest = rows[0]
    assert newest["title"] == "Anchors at Swingtacular"
    assert newest["session_date"] == "2026-10-01"
    assert newest["session_type"] == "group_class"
    assert (
        newest["pages"],
        newest["created"],
        newest["changed"],
        newest["removed"],
    ) == (3, 1, 1, 1)
    # A source since deleted keeps its revisions, with nothing to name it by.
    assert rows[1]["title"] is None and rows[1]["changed"] == 1
    assert rows[2]["created"] == 2 and rows[2]["title"] is None

    page = await _get(client, f"{REVISIONS}/sources", limit=1, offset=1)
    assert [r["source_id"] for r in page["data"]] == [str(gone)]
    assert page["meta"]["total"] == 3

    none = await _get(client, REVISIONS, source_id="none")
    assert sorted(r["slug"] for r in none["data"]) == ["anchor", "frame"]
    assert all(r["origin_source_id"] is None for r in none["data"])

    newest_first = await _get(client, REVISIONS, slug="anchor")
    assert [r["change"] for r in newest_first["data"]] == [
        "changed",
        "changed",
        "created",
    ]


async def test_a_bad_source_filter_or_revision_is_refused(client) -> None:
    assert (await client.get(REVISIONS, params={"source_id": "x"})).status_code == 422
    resp = await client.get(f"{REVISIONS}/999999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "revision_not_found"


# ── Thin entities ──


def _thin(slug: str, page: str, **over: Any) -> dict[str, Any]:
    entity = {
        "slug": slug,
        "name": slug.title(),
        "kind": "concept",
        "sources": 1,
        "references": 0,
        "page": page,
        "same": False,
        "definition": "",
        "quotes": [
            {
                "text": f"Said about {slug}.",
                "source_id": str(uuid.uuid4()),
                "session_date": "2026-10-01",
                "source_title": "A lesson",
            }
        ],
    }
    entity.update(over)
    return entity


async def _post_thin(client: httpx.AsyncClient, body: dict[str, Any]) -> dict:
    resp = await client.post(THIN, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_thin_entities_round_trip_in_page_then_slug_order(client) -> None:
    sent = [
        _thin("zeta", page="anchor"),
        _thin("beta", page="frame"),
        _thin("alpha", page="anchor", same=True),
    ]
    assert await _post_thin(client, {"entities": sent}) == {
        "written": 3,
        "removed": 0,
        "total": 3,
    }
    got = (await _get(client, THIN))["data"]["entities"]
    assert [e["slug"] for e in got] == ["alpha", "zeta", "beta"]
    assert got[0] == sent[2]


async def test_a_thin_write_replaces_whole_and_removes_what_is_gone(client) -> None:
    await _post_thin(
        client,
        {"entities": [_thin("a", "a"), _thin("b", "b"), _thin("c", "c")]},
    )
    result = await _post_thin(
        client,
        {
            "entities": [_thin("a", "x", quotes=[], definition="Now defined.")],
            # "a" is written as well as named: the write wins.
            "remove": ["a", "b", "never-was"],
        },
    )
    assert result == {"written": 1, "removed": 1, "total": 2}
    got = (await _get(client, THIN))["data"]["entities"]
    assert [e["slug"] for e in got] == ["c", "a"]
    assert got[1]["quotes"] == [] and got[1]["definition"] == "Now defined."


@pytest.fixture
async def reader_only_client(
    client, db_session: AsyncSession
) -> AsyncIterator[httpx.AsyncClient]:
    """The curator as it was: it reads the corpus, it cannot write the wiki."""
    principal = Principal(
        kind="machine", issuer="apikey", subject="corpus-only", display_name="c"
    )
    db_session.add(principal)
    await db_session.flush()
    db_session.add(
        PrincipalRole(
            principal_id=principal.id, role_name="corpus-reader", granted_by="test"
        )
    )
    await db_session.commit()
    original = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(
        return_value=VerifiedSubject(
            issuer="apikey", subject="corpus-only", kind="machine"
        )
    )
    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer k"},
        ) as c:
            yield c
    auth_mod.verify_bearer = original


async def test_reading_the_corpus_does_not_grant_writing_thin(
    reader_only_client,
) -> None:
    resp = await reader_only_client.post(THIN, json={"entities": [_thin("a", "a")]})
    assert resp.status_code == 403
    assert (await reader_only_client.get(THIN)).status_code == 200
    for path in (REVISIONS, f"{REVISIONS}/sources"):
        assert (await reader_only_client.get(path)).status_code == 200
