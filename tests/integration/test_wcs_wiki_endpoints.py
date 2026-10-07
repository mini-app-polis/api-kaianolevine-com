"""Tests for GET /v1/wcs/wiki/* read endpoints."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from identity.types import VerifiedSubject
from sqlalchemy import text

from kaianolevine_api import auth as auth_mod
from kaianolevine_api.main import app
from tests.integration.test_wcs_sources_endpoint import (
    _create_transcript,
    _source_payload,
)


def _vs(subject: str, kind: str = "human"):
    """A verified credential, stubbed.

    Verification moved to the identity binding and is tested there; these
    tests only need step 1 to have produced a subject.
    """
    return VerifiedSubject(
        issuer="https://clerk.kaianolevine.com",
        subject=subject,
        kind=kind,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
async def seed_dev_owner_wcs_admin(reset_db, async_engine) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('dev-owner', '', '', true) "
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
    entity_id = body["entity"]["id"]
    for attr in body["attributions"]:
        assert attr["entity_id"] == entity_id


async def test_list_concepts_paginated(client, seeded_source) -> None:
    resp = await client.get("/v1/wcs/wiki/concepts?limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] >= 1
    assert len(body["data"]) >= 1


# ---------------------------------------------------------------------------
# Contract tests (TEST-010) — one per remaining wiki list endpoint, asserting
# the {data, meta} envelope shape so every FastAPI route in this router has
# an explicit envelope-checking test.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/v1/wcs/wiki/techniques",
        "/v1/wcs/wiki/patterns",
        "/v1/wcs/wiki/drills",
    ],
)
async def test_list_entity_kinds_envelope_shape(client, seeded_source, path) -> None:
    """Contract: every wiki list endpoint returns the {data, meta} envelope."""
    resp = await client.get(f"{path}?limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert isinstance(body["data"], list)
    assert "total" in body["meta"]


async def test_list_instructors_envelope_shape(client, seeded_source) -> None:
    """Contract: GET /wcs/wiki/instructors returns the {data, meta} envelope."""
    resp = await client.get("/v1/wcs/wiki/instructors?limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert isinstance(body["data"], list)


async def test_list_sources_envelope_shape(client, seeded_source) -> None:
    """Contract: GET /wcs/wiki/sources returns the {data, meta} envelope."""
    resp = await client.get("/v1/wcs/wiki/sources?limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert isinstance(body["data"], list)


async def test_entity_not_found_error_envelope(client) -> None:
    """Contract: missing entity returns the {error: {code, message}} envelope."""
    resp = await client.get("/v1/wcs/wiki/concepts/no-such-slug")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]


async def test_get_instructor_view(client, seeded_source) -> None:
    resp = await client.get("/v1/wcs/wiki/instructors/kaiano")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["instructor"]["slug"] == "kaiano"
    assert data["referenced_in"] == []


async def test_get_source_view(client, seeded_source) -> None:
    source_id = seeded_source["id"]
    resp = await client.get(f"/v1/wcs/wiki/sources/{source_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"]["id"] == source_id
    assert len(data["attributions"]) >= 1
    frame = await client.get("/v1/wcs/wiki/concepts/frame")
    frame_id = frame.json()["data"]["entity"]["id"]
    frame_attrs = [a for a in data["attributions"] if a["entity_id"] == frame_id]
    assert len(frame_attrs) >= 1
    frame_attr = frame_attrs[0]
    assert frame_attr["entity_id"] == frame_id
    assert frame_attr["entity_slug"] == "frame"
    assert frame_attr["entity_name"] == "Frame"
    assert frame_attr["entity_kind"] == "concept"
    frame_def = next(d for d in data["definitions"] if d["entity_id"] == frame_id)
    assert frame_def["entity_slug"] == "frame"
    assert frame_def["entity_name"] == "Frame"
    assert frame_def["entity_kind"] == "concept"
    drill_rel = next(
        r
        for r in data["relations"]
        if r.get("relation_kind") == "drill_trains_technique"
    )
    assert drill_rel["from_entity_slug"] == "paper-drill"
    assert drill_rel["from_entity_name"] == "Paper Drill"
    assert drill_rel["to_entity_slug"] == "anchor-step"
    assert drill_rel["to_entity_name"] == "Anchor Step"
    drill_purpose = data["drill_purposes"][0]
    assert drill_purpose["drill_entity_slug"] == "paper-drill"
    assert drill_purpose["drill_entity_name"] == "Paper Drill"
    tech_req = data["technique_requirements"][0]
    assert tech_req["technique_entity_slug"] == "anchor-step"
    assert tech_req["technique_entity_name"] == "Anchor Step"
    refs = data["references"]
    assert len(refs) >= 1
    assert refs[0]["referenced_name"] == "Ben Morris"
    assert "instructor_id" not in refs[0]


async def test_export_shape(client, seeded_source) -> None:
    # The fixture caller holds wcs.corpus.read; the scope opens the
    # unfiltered corpus, not the caller being a machine.
    resp = await client.get("/v1/wcs/wiki/export")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "entities" in data
    assert "attributions" in data
    assert "exported_at" in data
    assert len(data["entities"]) >= 1
    entity_ids = {e["id"] for e in data["entities"]}
    for attr in data["attributions"]:
        assert attr["entity_id"] in entity_ids
    if data["references"]:
        assert "referenced_name" in data["references"][0]
        assert "instructor_id" not in data["references"][0]


async def test_entity_not_found(client) -> None:
    resp = await client.get("/v1/wcs/wiki/concepts/no-such-slug")
    assert resp.status_code == 404


async def test_admin_list_sources_returns_all(client) -> None:
    transcript_id = await _create_transcript(client)
    private_resp = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            transcript_id,
            is_default_visible=False,
            visibility="private",
            title="Private lesson",
        ),
    )
    assert private_resp.status_code == 200
    private_id = private_resp.json()["data"]["id"]

    public_resp = await client.post(
        "/v1/wcs/transcripts",
        json={
            "raw_text": "Public lesson transcript.",
            "source_type": "plaud",
            "source_filename": "public-lesson.txt",
            "drive_file_id": "drive-public-lesson",
        },
    )
    assert public_resp.status_code == 201
    public_tid = public_resp.json()["data"]["id"]
    public_resp = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            public_tid,
            is_default_visible=True,
            title="Public lesson",
        ),
    )
    assert public_resp.status_code == 200
    public_id = public_resp.json()["data"]["id"]

    admin_list = await client.get("/v1/wcs/wiki/admin/sources?limit=100")
    assert admin_list.status_code == 200
    admin_ids = {s["id"] for s in admin_list.json()["data"]}
    assert private_id in admin_ids
    assert public_id in admin_ids

    original_verify = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(return_value=_vs("stranger-user", "human"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": "Bearer stranger-token"},
    ) as stranger:
        forbidden = await stranger.get("/v1/wcs/wiki/admin/sources")
        assert forbidden.status_code == 403
    auth_mod.verify_bearer = original_verify


async def test_admin_get_private_source(client) -> None:
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

    admin_resp = await client.get(f"/v1/wcs/wiki/admin/sources/{source_id}")
    assert admin_resp.status_code == 200
    assert admin_resp.json()["data"]["source"]["id"] == source_id

    original_verify = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(return_value=_vs("stranger-user", "human"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": "Bearer stranger-token"},
    ) as stranger:
        forbidden = await stranger.get(f"/v1/wcs/wiki/admin/sources/{source_id}")
        assert forbidden.status_code == 403
    auth_mod.verify_bearer = original_verify


async def test_admin_caller_scoped_list_matches_regular_user(client) -> None:
    """Admin on GET /wiki/sources sees only default-visible sources (no bypass)."""
    transcript_id = await _create_transcript(client)
    private_resp = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            transcript_id,
            is_default_visible=False,
            visibility="private",
        ),
    )
    assert private_resp.status_code == 200
    private_id = private_resp.json()["data"]["id"]

    public_resp = await client.post(
        "/v1/wcs/transcripts",
        json={
            "raw_text": "Another transcript.",
            "source_type": "plaud",
            "source_filename": "visible-lesson.txt",
            "drive_file_id": "drive-visible-lesson",
        },
    )
    assert public_resp.status_code == 201
    public_tid = public_resp.json()["data"]["id"]
    public_resp = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(public_tid, is_default_visible=True),
    )
    assert public_resp.status_code == 200
    public_id = public_resp.json()["data"]["id"]

    resp = await client.get("/v1/wcs/wiki/sources?limit=100")
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["data"]}
    assert public_id in ids
    assert private_id not in ids


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

    original_verify = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(return_value=_vs("stranger-user", "human"))
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('stranger-user', '', '', false) "
                "ON CONFLICT (user_id) DO NOTHING"
            )
        )
        # A profile row no longer confers anything; the principal does. This
        # caller is a legitimate reader who simply cannot see a private
        # source — 404, not 403. Without the principal the request would be
        # refused before visibility was ever consulted.
        await conn.execute(
            text(
                "INSERT INTO identity_principals (id, kind, issuer, subject, "
                "display_name, status) VALUES "
                "('11111111111141118111111111111111', 'human', "
                "'https://clerk.kaianolevine.com', 'stranger-user', '', 'active')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO identity_principal_roles (principal_id, role_name, "
                "granted_by) VALUES "
                "('11111111111141118111111111111111', 'wcs-reader', 'test')"
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
        assert export.status_code == 403  # JWT callers cannot bulk-export
    auth_mod.verify_bearer = original_verify


async def test_list_entities_status_filter_narrows_rows_and_total(
    client, seeded_source, async_engine
) -> None:
    """Composed entities start as stubs; ?status selects one lifecycle state."""
    before = await client.get("/v1/wcs/wiki/concepts")
    all_slugs = {e["slug"] for e in before.json()["data"]}
    assert "frame" in all_slugs
    async with async_engine.begin() as conn:
        await conn.execute(
            text("UPDATE wcs_entities SET status = 'mature' WHERE slug = 'frame'")
        )

    mature = await client.get("/v1/wcs/wiki/concepts", params={"status": "mature"})
    assert mature.status_code == 200
    assert [e["slug"] for e in mature.json()["data"]] == ["frame"]
    assert mature.json()["meta"]["total"] == 1

    stubs = await client.get("/v1/wcs/wiki/concepts", params={"status": "stub"})
    assert {e["slug"] for e in stubs.json()["data"]} == all_slugs - {"frame"}
    assert stubs.json()["meta"]["total"] == len(all_slugs) - 1

    # The filter applies within the kind: frame is a concept, not a technique.
    none = await client.get("/v1/wcs/wiki/techniques", params={"status": "mature"})
    assert none.json()["data"] == []
    assert none.json()["meta"]["total"] == 0


async def test_get_instructor_unknown_slug_returns_404(client) -> None:
    resp = await client.get("/v1/wcs/wiki/instructors/no-such-instructor")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "instructor_not_found"


@pytest.mark.parametrize(
    "path", ["/v1/wcs/wiki/sources/{id}", "/v1/wcs/wiki/admin/sources/{id}"]
)
async def test_get_unknown_source_returns_404(client, path: str) -> None:
    resp = await client.get(path.format(id=uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "source_not_found"
