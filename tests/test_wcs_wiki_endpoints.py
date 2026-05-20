"""Tests for GET /v1/wcs/wiki/* read endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text

from kaianolevine_api import auth as auth_mod
from kaianolevine_api.main import app
from tests.test_wcs_sources_endpoint import _create_transcript, _source_payload


@pytest.fixture(autouse=True)
async def seed_dev_owner_wcs_admin(reset_db, async_engine) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('dev-owner', '', '', 1) "
                "ON CONFLICT (user_id) DO UPDATE SET is_admin = excluded.is_admin"
            )
        )


@pytest.fixture
async def seeded_source(client) -> dict:
    transcript_id = await _create_transcript(client)
    resp = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            transcript_id,
            is_default_visible=True,
            raw_output={
                "entities": [
                    {"kind": "concept", "name": "Frame", "prose": "Connection."},
                    {"kind": "technique", "name": "Anchor Step", "prose": "Grounded."},
                    {"kind": "pattern", "name": "Sugar Push", "prose": "Classic."},
                    {"kind": "drill", "name": "Paper Drill", "prose": "Walk."},
                ],
                "entity_definitions": [
                    {"entity_name": "Frame", "definition": "Upper body."},
                ],
                "entity_relations": [
                    {
                        "from": "Paper Drill",
                        "to": "Anchor Step",
                        "relation_kind": "drill_trains_technique",
                    }
                ],
                "drill_purposes": [
                    {
                        "drill_name": "Paper Drill",
                        "skill_description": "Balance",
                    }
                ],
                "technique_requirements": [
                    {
                        "technique_name": "Anchor Step",
                        "skill_description": "Balance",
                    }
                ],
                "references": [{"name": "Ben Morris", "type": "judge"}],
            },
        ),
    )
    assert resp.status_code == 200
    return resp.json()["data"]


@pytest.mark.parametrize(
    ("path", "slug_key"),
    [
        ("/v1/wcs/wiki/concepts/frame", "frame"),
        ("/v1/wcs/wiki/techniques/anchor-step", "anchor-step"),
        ("/v1/wcs/wiki/patterns/sugar-push", "sugar-push"),
        ("/v1/wcs/wiki/drills/paper-drill", "paper-drill"),
    ],
)
async def test_get_entity_views(
    client, seeded_source, path: str, slug_key: str
) -> None:
    resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["entity"]["slug"] == slug_key
    assert isinstance(body["attributions"], list)


async def test_list_concepts_paginated(client, seeded_source) -> None:
    resp = await client.get("/v1/wcs/wiki/concepts?limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] >= 1
    assert len(body["data"]) >= 1


async def test_get_instructor_view(client, seeded_source) -> None:
    resp = await client.get("/v1/wcs/wiki/instructors/kaiano")
    assert resp.status_code == 200
    assert resp.json()["data"]["instructor"]["slug"] == "kaiano"


async def test_get_source_view(client, seeded_source) -> None:
    source_id = seeded_source["id"]
    resp = await client.get(f"/v1/wcs/wiki/sources/{source_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"]["id"] == source_id
    assert len(data["attributions"]) >= 1


async def test_export_shape(client, seeded_source) -> None:
    resp = await client.get("/v1/wcs/wiki/export")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "entities" in data
    assert "attributions" in data
    assert "exported_at" in data
    assert len(data["entities"]) >= 1


async def test_entity_not_found(client) -> None:
    resp = await client.get("/v1/wcs/wiki/concepts/no-such-slug")
    assert resp.status_code == 404


async def test_visibility_filters_private_source(client, async_engine) -> None:
    transcript_id = await _create_transcript(client)
    create = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            transcript_id,
            is_default_visible=False,
            visibility="private",
        ),
    )
    source_id = create.json()["data"]["id"]

    original_verify = auth_mod.verify_clerk_jwt
    auth_mod.verify_clerk_jwt = AsyncMock(return_value="stranger-user")
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('stranger-user', '', '', 0) "
                "ON CONFLICT (user_id) DO NOTHING"
            )
        )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": "Bearer stranger-token"},
    ) as stranger:
        resp = await stranger.get(f"/v1/wcs/wiki/sources/{source_id}")
        assert resp.status_code == 404
        export = await stranger.get("/v1/wcs/wiki/export")
        assert export.status_code == 200
        ids = {s["id"] for s in export.json()["data"]["sources"]}
        assert source_id not in ids
    auth_mod.verify_clerk_jwt = original_verify
