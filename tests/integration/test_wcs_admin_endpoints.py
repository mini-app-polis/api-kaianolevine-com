"""Tests for WCS admin correction/addition/recompose endpoints."""

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
async def stranger_client(client):  # noqa: ARG001
    original_verify = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(return_value=_vs("stranger-user", "human"))
    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer stranger-token"},
        ) as c:
            yield c
    auth_mod.verify_bearer = original_verify


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


async def test_name_correction_scoped_to_a_source_recomposes_it(
    client, source_id: str
) -> None:
    """A source-scoped correction is applied at once, not deferred.

    The recompose resolves the source's raw instructor "Kaiano" through the
    correction, so the corrected instructor exists as soon as the call returns.
    """
    before = await client.get("/v1/wcs/wiki/instructors/kaiano-levine")
    assert before.status_code == 404

    resp = await client.post(
        "/v1/wcs/admin/corrections/name",
        json={
            "raw_name": "Kaiano",
            "corrected_name": "Kaiano Levine",
            "scope": "source",
            "source_id": source_id,
            "reason": "Full name.",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["deferred"] is False
    assert data["message"] == ""
    assert [str(x) for x in data["recomposed_source_ids"]] == [source_id]

    after = await client.get("/v1/wcs/wiki/instructors/kaiano-levine")
    assert after.status_code == 200, after.text
    assert after.json()["data"]["instructor"]["canonical_name"] == "Kaiano Levine"


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
            "corrected_value": "Anchor lesson — admin updated",
            "reason": "Test correction.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert body["data"]["field"] == "title"
    assert source_id in [str(x) for x in body["data"]["recomposed_source_ids"]]


async def test_drill_purpose_addition_envelope_shape(
    client, rich_source_id: str
) -> None:
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


async def test_technique_requirement_addition_envelope_shape(
    client, rich_source_id: str
) -> None:
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


async def test_entity_relation_addition_envelope_shape(
    client, rich_source_id: str
) -> None:
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


async def test_patch_source_visibility_reflects_in_caller_list(
    client, async_engine
) -> None:
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

    # The caller in this test is an ordinary reader, not a stranger with no
    # standing: the point is that a private source is hidden from someone who
    # may legitimately read, and appears once visibility changes. Without a
    # principal they are refused before visibility is ever consulted.
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO identity_principals (id, kind, issuer, subject, "
                "display_name, status) VALUES "
                "('22222222222242228222222222222222', 'human', "
                "'https://clerk.kaianolevine.com', 'stranger-user', '', 'active')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO identity_principal_roles (principal_id, role_name, "
                "granted_by) VALUES "
                "('22222222222242228222222222222222', 'wcs-reader', 'test')"
            )
        )

    original_verify = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(return_value=_vs("stranger-user", "human"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": "Bearer stranger-token"},
    ) as stranger:
        before = await stranger.get("/v1/wcs/wiki/sources?limit=100")
        assert source_id not in {s["id"] for s in before.json()["data"]}
    auth_mod.verify_bearer = original_verify

    patch = await client.patch(
        f"/v1/wcs/admin/sources/{source_id}/visibility",
        json={"is_default_visible": True},
    )
    assert patch.status_code == 200
    assert patch.json()["data"]["is_default_visible"] is True

    auth_mod.verify_bearer = AsyncMock(return_value=_vs("stranger-user", "human"))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": "Bearer stranger-token"},
    ) as stranger:
        after = await stranger.get("/v1/wcs/wiki/sources?limit=100")
        assert source_id in {s["id"] for s in after.json()["data"]}
    auth_mod.verify_bearer = original_verify


async def test_patch_source_admin_partial_update(client, source_id: str) -> None:
    before = await client.get(f"/v1/wcs/wiki/admin/sources/{source_id}")
    assert before.status_code == 200
    original = before.json()["data"]["source"]

    patch = await client.patch(
        f"/v1/wcs/admin/sources/{source_id}",
        json={"title": "Admin retitled"},
    )
    assert patch.status_code == 200
    data = patch.json()["data"]
    assert data["title"] == "Admin retitled"
    assert data["session_type"] == original["session_type"]
    assert data["instructors_raw"] == original["instructors_raw"]


async def test_patch_source_endpoints_forbid_non_admin(
    source_id: str, stranger_client
) -> None:
    vis = await stranger_client.patch(
        f"/v1/wcs/admin/sources/{source_id}/visibility",
        json={"is_default_visible": True},
    )
    assert vis.status_code == 403
    meta = await stranger_client.patch(
        f"/v1/wcs/admin/sources/{source_id}",
        json={"title": "nope"},
    )
    assert meta.status_code == 403


async def test_patch_source_endpoints_404_missing(client) -> None:
    missing = uuid.uuid4()
    vis = await client.patch(
        f"/v1/wcs/admin/sources/{missing}/visibility",
        json={"is_default_visible": True},
    )
    assert vis.status_code == 404
    meta = await client.patch(
        f"/v1/wcs/admin/sources/{missing}",
        json={"title": "nope"},
    )
    assert meta.status_code == 404


async def test_recompose_unknown_source_returns_404(client) -> None:
    resp = await client.post(f"/v1/wcs/admin/recompose/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "source_not_found"


async def test_gaps_skills_unpaired_names_the_side_each_skill_is_on(
    client, rich_source_id: str
) -> None:
    """A drill skill with no technique counterpart, and vice versa, are both gaps."""
    drill = await client.post(
        "/v1/wcs/admin/additions/drill_purpose",
        json={
            "drill_entity_slug": "paper-drill",
            "source_id": rich_source_id,
            "skill_name": "Balance",
            "prose": "Train weight commitment.",
        },
    )
    assert drill.status_code == 200, drill.text
    tech = await client.post(
        "/v1/wcs/admin/additions/technique_requirement",
        json={
            "technique_entity_slug": "anchor-step",
            "source_id": rich_source_id,
            "skill_name": "Posture",
            "prose": "Anchor step requires posture.",
        },
    )
    assert tech.status_code == 200, tech.text

    resp = await client.get("/v1/wcs/admin/gaps/skills-unpaired")
    assert resp.status_code == 200
    details = {(g["slug"], g["detail"]) for g in resp.json()["data"]}
    assert details == {
        ("balance", "drill only: Balance"),
        ("posture", "technique only: Posture"),
    }
    assert all(g["kind"] == "skill" for g in resp.json()["data"])

    # Pairing the drill's skill on the technique side closes that gap.
    pair = await client.post(
        "/v1/wcs/admin/additions/technique_requirement",
        json={
            "technique_entity_slug": "anchor-step",
            "source_id": rich_source_id,
            "skill_name": "Balance",
            "prose": "Anchor step requires balance.",
        },
    )
    assert pair.status_code == 200
    after = await client.get("/v1/wcs/admin/gaps/skills-unpaired")
    assert [g["slug"] for g in after.json()["data"]] == ["posture"]


# ---------------------------------------------------------------------------
# Metadata corrections carry the field's plain value and are applied.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "corrected_value", "source_key", "expected"),
    [
        ("title", "Retitled lesson", "title", "Retitled lesson"),
        ("organization", "Twin Cities WCS", "organization", "Twin Cities WCS"),
        ("session_date", "2024-02-20", "session_date", "2024-02-20"),
        ("session_type", "group_class", "session_type", "group_class"),
        ("instructors", ["Kaiano", "Amy"], "instructors_raw", ["Kaiano", "Amy"]),
        ("students", ["Sarah", "Kate"], "students_raw", ["Sarah", "Kate"]),
        ("visibility", "public", "visibility", "public"),
        ("is_default_visible", True, "is_default_visible", True),
    ],
)
async def test_metadata_correction_applies_every_supported_field(
    client,
    source_id: str,
    field: str,
    corrected_value: object,
    source_key: str,
    expected: object,
) -> None:
    """Every allowed field reaches the apply step and changes the source row."""
    resp = await client.post(
        "/v1/wcs/admin/corrections/metadata",
        json={
            "source_id": source_id,
            "field": field,
            "corrected_value": corrected_value,
        },
    )
    assert resp.status_code == 200, resp.text

    view = await client.get(f"/v1/wcs/wiki/admin/sources/{source_id}")
    assert view.status_code == 200
    assert view.json()["data"]["source"][source_key] == expected


@pytest.mark.parametrize(
    ("field", "corrected_value"),
    [
        ("filename", "x.txt"),
        ("filename", {"filename": "x.txt"}),
        ("title", {"title": "Dict form"}),
        ("title", True),
        ("organization", ["a"]),
        ("session_date", "not-a-date"),
        ("session_date", 20240220),
        ("session_type", "workshop"),
        ("visibility", "secret"),
        ("is_default_visible", "true"),
        ("is_default_visible", 1),
        ("instructors", "Kaiano"),
        ("instructors", ["Kaiano", ""]),
        ("students", [1, 2]),
    ],
)
async def test_metadata_correction_rejects_mismatched_value(
    client, async_engine, source_id: str, field: str, corrected_value: object
) -> None:
    """A field the API cannot apply, or a value of the wrong shape, is a 422.

    Nothing is written: the correction row would otherwise sit in the input
    layer looking authoritative while the source never changed.
    """
    resp = await client.post(
        "/v1/wcs/admin/corrections/metadata",
        json={
            "source_id": source_id,
            "field": field,
            "corrected_value": corrected_value,
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"
    async with async_engine.begin() as conn:
        count = (
            await conn.execute(
                text("SELECT count(*) FROM wcs_source_metadata_corrections")
            )
        ).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# Writes naming a source that does not exist answer 404 before writing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/v1/wcs/admin/corrections/name",
            {
                "raw_name": "Kaiano",
                "corrected_name": "Kaiano Levine",
                "scope": "source",
            },
        ),
        (
            "/v1/wcs/admin/corrections/name",
            {
                "raw_name": "Kaiano",
                "corrected_name": "Kaiano Levine",
                "scope": "global",
            },
        ),
        (
            "/v1/wcs/admin/corrections/attribution",
            {
                "attribution_target": {"raw_term": "Settle", "position": 0},
                "field": "prose",
                "corrected_value": {"prose": "x"},
            },
        ),
        (
            "/v1/wcs/admin/corrections/metadata",
            {"field": "title", "corrected_value": "x"},
        ),
        (
            "/v1/wcs/admin/additions/attribution",
            {"entity_slug": "settle", "prose": "x"},
        ),
        (
            "/v1/wcs/admin/additions/drill_purpose",
            {"drill_entity_slug": "paper-drill", "skill_name": "Balance"},
        ),
        (
            "/v1/wcs/admin/additions/technique_requirement",
            {"technique_entity_slug": "anchor-step", "skill_name": "Balance"},
        ),
    ],
)
async def test_admin_write_with_unknown_source_returns_404(
    client, rich_source_id: str, path: str, body: dict
) -> None:
    """An unknown source_id is the caller's mistake, not a server failure."""
    resp = await client.post(path, json={**body, "source_id": str(uuid.uuid4())})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "source_not_found"


async def test_source_scoped_name_correction_requires_source_id(
    client, async_engine
) -> None:
    """scope "source" without a source_id is a 422, not a silent no-op.

    Such a row matched neither the per-source nor the global lookup, so it
    was saved, reported as a global correction, and never applied.
    """
    resp = await client.post(
        "/v1/wcs/admin/corrections/name",
        json={
            "raw_name": "Kaiano",
            "corrected_name": "Kaiano Levine",
            "scope": "source",
        },
    )
    assert resp.status_code == 422, resp.text
    async with async_engine.begin() as conn:
        count = (
            await conn.execute(text("SELECT count(*) FROM wcs_name_corrections"))
        ).scalar_one()
    assert count == 0
