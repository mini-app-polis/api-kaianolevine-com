"""The wiki curator's state: documents round-trip, writes upsert, scopes hold.

The state is what the curator paid model calls for, so the store's one job
is to give back exactly what it was given: every field, every reference and
revision in order, nothing duplicated by a re-sent import.
"""

from __future__ import annotations

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

KEY_A = "attribution:0012e47a-7fce-4c01-9322-0a446fb8e05f"
KEY_B = "definition:30f62150-c875-445e-a0c7-33c3c924548b"
ENTITY = "30f62150-c875-445e-a0c7-33c3c924548b"
SOURCE = "b436c89d-72ba-4d4d-8bef-f6957bd3643f"


def _statement(text: str, refs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "linker_version": "3",
        "model": "anthropic/claude-sonnet-4-6",
        "subject_slug": "closed-position-frame",
        "source_id": SOURCE,
        "session_date": "2026-09-16",
        "text": text,
        "references": refs,
    }


def _ref(slug: str, polarity: str = "assert", entity: str | None = None) -> dict:
    return {
        "name": slug.replace("-", " "),
        "target_slug": slug,
        "target_entity_id": entity,
        "polarity": polarity,
    }


LINKS = {
    "linker_version": "3",
    "model": "anthropic/claude-sonnet-4-6",
    "statements": {
        KEY_A: _statement(
            "Connect at the shoulder blade.",
            [_ref("connection"), _ref("frame", "deny", ENTITY), _ref("leading")],
        ),
        KEY_B: _statement("Stated, referring to nothing.", []),
    },
}

FACETS = {
    "consolidator_versions": {"concept": "5", "drill": "5", "pattern": "5"},
    "model": "m",
    "entities": {
        "stationary-anchor": {
            "parent": "anchor",
            "same": False,
            "reason": "a kind of anchor",
            "version": "5",
            "model": "m",
            "decided_at": "2026-10-16",
        },
        "anchor": {
            "parent": None,
            "same": False,
            "reason": "",
            "version": "5",
            "model": "m",
            "decided_at": "2026-10-16",
        },
    },
}

PROSE = {
    "generator_version": "4",
    "model": "m",
    "entities": {
        "anchor": {
            "entity_id": ENTITY,
            "generator_version": "4",
            "model": "m",
            "generated_at": "2026-10-16",
            "definition": "The settle at the end of a pattern.",
            "context": "",
            "integrated": [KEY_B, KEY_A][::-1],
            "revisions": [
                {
                    "by": "llm",
                    "at": "2026-10-15",
                    "model": "m",
                    "statements": 2,
                    "prose": "The earliest shape.",
                },
                {
                    "by": "page",
                    "at": "2026-10-16",
                    "definition": "Edited on the page.",
                    "context": "",
                },
                {
                    "by": "llm",
                    "at": "2026-10-16",
                    "model": "m",
                    "statements": 1,
                    "definition": "The settle at the end of a pattern.",
                    "context": "",
                },
            ],
        }
    },
}


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


async def _get(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    resp = await client.get(f"/v1/wcs/wiki/state/{name}")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _post(client: httpx.AsyncClient, name: str, doc: dict) -> dict[str, Any]:
    resp = await client.post(f"/v1/wcs/wiki/state/{name}", json=doc)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.mark.parametrize(
    ("name", "doc"), [("links", LINKS), ("facets", FACETS), ("prose", PROSE)]
)
async def test_a_document_comes_back_as_it_was_sent(client, name, doc) -> None:
    assert await _post(client, name, doc) == {
        "written": 2 if name != "prose" else 1,
        "total": 2 if name != "prose" else 1,
    }
    assert _drop_none(await _get(client, name)) == _drop_none(doc)


@pytest.mark.parametrize(
    ("name", "doc"), [("links", LINKS), ("facets", FACETS), ("prose", PROSE)]
)
async def test_resending_an_import_changes_nothing(client, name, doc) -> None:
    await _post(client, name, doc)
    first = await _get(client, name)
    again = await _post(client, name, doc)
    assert again["total"] == again["written"]
    assert await _get(client, name) == first


async def test_an_empty_store_reads_as_empty_documents(client) -> None:
    assert await _get(client, "links") == {
        "linker_version": "",
        "model": "",
        "statements": {},
    }
    assert (await _get(client, "facets"))["entities"] == {}
    assert (await _get(client, "prose"))["entities"] == {}


async def test_a_chunked_import_adds_up_and_leaves_earlier_chunks_alone(
    client,
) -> None:
    first = {**LINKS, "statements": {KEY_A: LINKS["statements"][KEY_A]}}
    second = {**LINKS, "statements": {KEY_B: LINKS["statements"][KEY_B]}}
    assert (await _post(client, "links", first)) == {"written": 1, "total": 1}
    assert (await _post(client, "links", second)) == {"written": 1, "total": 2}
    assert _drop_none(await _get(client, "links")) == _drop_none(LINKS)


async def test_an_entry_is_replaced_whole_children_included(client) -> None:
    await _post(client, "links", LINKS)
    await _post(client, "prose", PROSE)

    relinked = _statement("Relinked under a new version.", [_ref("anchor")])
    relinked["linker_version"] = "4"
    await _post(
        client,
        "links",
        {"linker_version": "4", "model": "m2", "statements": {KEY_A: relinked}},
    )
    links = await _get(client, "links")
    assert links["linker_version"] == "4" and links["model"] == "m2"
    assert [r["target_slug"] for r in links["statements"][KEY_A]["references"]] == [
        "anchor"
    ]
    assert links["statements"][KEY_B]["linker_version"] == "3"  # untouched

    entry = {
        **PROSE["entities"]["anchor"],
        "revisions": PROSE["entities"]["anchor"]["revisions"][:1],
    }
    await _post(client, "prose", {**PROSE, "entities": {"anchor": entry}})
    assert len((await _get(client, "prose"))["entities"]["anchor"]["revisions"]) == 1


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


@pytest.mark.parametrize("name", ["links", "facets", "prose"])
async def test_reading_the_corpus_does_not_grant_writing_the_wiki(
    reader_only_client, name
) -> None:
    doc = {"links": LINKS, "facets": FACETS, "prose": PROSE}[name]
    resp = await reader_only_client.post(f"/v1/wcs/wiki/state/{name}", json=doc)
    assert resp.status_code == 403
    assert (
        await reader_only_client.get(f"/v1/wcs/wiki/state/{name}")
    ).status_code == 200
