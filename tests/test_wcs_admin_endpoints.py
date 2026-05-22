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


# ---------------------------------------------------------------------------
# Contract tests (TEST-010) — every admin endpoint asserts the response
# envelope shape on success and the error envelope shape on failure.
# ---------------------------------------------------------------------------


@pytest.fixture
async def rich_source_id(client) -> str:
    """A source seeded with a concept, technique, pattern, and drill entity.

    Provides slugs (`settle`, `anchor-step`, `sugar-push`, `paper-drill`) for
    the admin-addition contract tests that need to reference an entity that
    actually exists in the substrate.
    """
    transcript_id = await _create_transcript(client)
    resp = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            transcript_id,
            raw_output={
                "entities": [
                    {"kind": "concept", "name": "Settle", "prose": "Drop into floor."},
                    {"kind": "technique", "name": "Anchor Step", "prose": "Grounded."},
                    {"kind": "pattern", "name": "Sugar Push", "prose": "Classic."},
                    {"kind": "drill", "name": "Paper Drill", "prose": "Walk."},
                ],
            },
        ),
    )
    assert resp.status_code == 200
    return resp.json()["data"]["id"]


async def test_metadata_correction_envelope_shape(client, source_id: str) -> None:
    """Contract: POST /wcs/admin/corrections/metadata returns {data, meta} on success."""
    resp = await client.post(
        "/v1/wcs/admin/corrections/metadata",
        json={
            "source_id": source_id,
            "field": "title",
            "corrected_value": {"title": "Anchor lesson — admin updated"},
            "reason": "Test correction.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert body["data"]["field"] == "title"
    assert source_id in [str(x) for x in body["data"]["recomposed_source_ids"]]


async def test_drill_purpose_addition_envelope_shape(client, rich_source_id: str) -> None:
    """Contract: POST /wcs/admin/additions/drill_purpose returns {data, meta} on success."""
    resp = await client.post(
        "/v1/wcs/admin/additions/drill_purpose",
        json={
            "drill_entity_slug": "paper-drill",
            "source_id": rich_source_id,
            "skill_name": "Balance",
            "prose": "Train weight commitment.",
            "focus_context": "follower",
            "reason": "Manual addition.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert "id" in body["data"]
    assert "recomposed_source_ids" in body["data"]


async def test_technique_requirement_addition_envelope_shape(client, rich_source_id: str) -> None:
    """Contract: POST /wcs/admin/additions/technique_requirement returns {data, meta} on success."""
    resp = await client.post(
        "/v1/wcs/admin/additions/technique_requirement",
        json={
            "technique_entity_slug": "anchor-step",
            "source_id": rich_source_id,
            "skill_name": "Balance",
            "prose": "Anchor step requires settle.",
            "reason": "Manual addition.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert "id" in body["data"]
    assert "recomposed_source_ids" in body["data"]


async def test_entity_relation_addition_envelope_shape(client, rich_source_id: str) -> None:
    """Contract: POST /wcs/admin/additions/entity_relation returns {data, meta} on success."""
    resp = await client.post(
        "/v1/wcs/admin/additions/entity_relation",
        json={
            "from_entity_slug": "anchor-step",
            "to_entity_slug": "settle",
            "relation_kind": "depends_on",
            "prose": "Anchor depends on settle.",
            "reason": "Manual addition.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert "id" in body["data"]


async def test_gaps_stub_entities_envelope_shape(client, source_id: str) -> None:
    """Contract: GET /wcs/admin/gaps/stub-entities returns {data, meta} on success."""
    resp = await client.get("/v1/wcs/admin/gaps/stub-entities")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert isinstance(body["data"], list)


async def test_gaps_skills_unpaired_envelope_shape(client, source_id: str) -> None:
    """Contract: GET /wcs/admin/gaps/skills-unpaired returns {data, meta} on success."""
    resp = await client.get("/v1/wcs/admin/gaps/skills-unpaired")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert isinstance(body["data"], list)


async def test_gaps_sources_uncomposed_envelope_shape(client, source_id: str) -> None:
    """Contract: GET /wcs/admin/gaps/sources-uncomposed returns {data, meta} on success."""
    resp = await client.get("/v1/wcs/admin/gaps/sources-uncomposed")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert isinstance(body["data"], list)


async def test_admin_endpoints_error_envelope_on_forbidden(stranger_client) -> None:
    """Contract: admin endpoints return {error: {code, message}} for non-admin callers."""
    resp = await stranger_client.get("/v1/wcs/admin/gaps/orphan-entities")
    assert resp.status_code == 403
    body = resp.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]
