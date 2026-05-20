"""Tests for WCS admin correction/addition/recompose endpoints."""

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
async def stranger_client(client):  # noqa: ARG001
    original_verify = auth_mod.verify_clerk_jwt
    auth_mod.verify_clerk_jwt = AsyncMock(return_value="stranger-user")
    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer stranger-token"},
        ) as c:
            yield c
    auth_mod.verify_clerk_jwt = original_verify


@pytest.fixture
async def source_id(client) -> str:
    transcript_id = await _create_transcript(client)
    resp = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            transcript_id,
            raw_output={
                "entities": [{"kind": "concept", "name": "Settle", "prose": "x"}],
            },
        ),
    )
    assert resp.status_code == 200
    return resp.json()["data"]["id"]


async def test_name_correction_global_deferred(client) -> None:
    resp = await client.post(
        "/v1/wcs/admin/corrections/name",
        json={
            "raw_name": "Roberta",
            "corrected_name": "Robert",
            "scope": "global",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["deferred"] is True
    assert data["recomposed_source_ids"] == []


async def test_attribution_correction_recomposes(client, source_id: str) -> None:
    resp = await client.post(
        "/v1/wcs/admin/corrections/attribution",
        json={
            "source_id": source_id,
            "attribution_target": {"raw_term": "Settle", "position": 0},
            "field": "prose",
            "corrected_value": {"prose": "Admin corrected."},
        },
    )
    assert resp.status_code == 200
    assert source_id in [str(x) for x in resp.json()["data"]["recomposed_source_ids"]]


async def test_attribution_addition_recomposes(client, source_id: str) -> None:
    resp = await client.post(
        "/v1/wcs/admin/additions/attribution",
        json={
            "source_id": source_id,
            "entity_slug": "settle",
            "prose": "Manual addition.",
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]["recomposed_source_ids"]) >= 1


async def test_recompose_endpoint_returns_counts(client, source_id: str) -> None:
    resp = await client.post(f"/v1/wcs/admin/recompose/{source_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source_id"] == source_id
    assert data["attributions_written"] >= 1


async def test_admin_endpoints_forbid_non_admin(stranger_client) -> None:
    resp = await stranger_client.post(
        "/v1/wcs/admin/corrections/name",
        json={"raw_name": "a", "corrected_name": "b"},
    )
    assert resp.status_code == 403


async def test_gaps_orphan_entities(client, source_id: str) -> None:
    resp = await client.get("/v1/wcs/admin/gaps/orphan-entities")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)
