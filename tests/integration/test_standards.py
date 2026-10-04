"""Tests for the standards catalog routes.

The behaviours worth pinning are the two that are policy rather than
plumbing: a published version is immutable, and "latest" means the highest
version rather than the most recent insert.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


def catalog(
    version: str = "6.13.0",
    *,
    compiled_at: str = "2026-09-11T20:00:00Z",
    rule_title: str = "Base images are pinned by digest",
) -> dict:
    """A minimal catalog document in the shape the compiler emits."""
    return {
        "version": version,
        "compiled_at": compiled_at,
        "rule_count": 2,
        "dimensions": {"cd_readiness": "Deployment readiness."},
        "statuses": {"requirement": "Must hold."},
        "schema": {"repo_types": {"api-service": "A FastAPI service."}},
        "rules": [
            {
                "id": "CD-022",
                "domain": "delivery",
                "title": rule_title,
                "checkable": True,
                "check_mode": "deterministic",
                "applies_to": ["all"],
            },
            {
                "id": "OPS-001",
                "domain": "operations",
                "title": "Retired, kept visible",
                "checkable": False,
                "check_mode": None,
                "applies_to": None,
            },
        ],
    }


async def test_publish_then_read_latest(client):
    published = await client.post("/v1/standards/catalog", json=catalog())
    assert published.status_code == 200, published.text
    body = published.json()["data"]
    assert body["version"] == "6.13.0"
    assert body["rule_count"] == 2
    assert body["created"] is True

    fetched = await client.get("/v1/standards/catalog")
    assert fetched.status_code == 200
    document = fetched.json()["data"]
    assert document["version"] == "6.13.0"
    assert len(document["rules"]) == 2
    # Blocks the API does not model are stored and returned verbatim.
    assert document["schema"]["repo_types"] == {"api-service": "A FastAPI service."}
    # A checkable: false rule survives the round trip rather than being
    # filtered out — the whole reason the compiler includes them.
    assert any(rule["checkable"] is False for rule in document["rules"])


async def test_latest_is_highest_version_not_newest_row(client):
    """6.9.0 published after 6.13.0 must not become 'latest'."""
    await client.post("/v1/standards/catalog", json=catalog("6.13.0"))
    await client.post("/v1/standards/catalog", json=catalog("6.9.0"))

    latest = await client.get("/v1/standards/catalog")
    assert latest.json()["data"]["version"] == "6.13.0"


async def test_get_specific_version(client):
    await client.post("/v1/standards/catalog", json=catalog("6.13.0"))
    await client.post("/v1/standards/catalog", json=catalog("6.12.0"))

    fetched = await client.get("/v1/standards/catalog", params={"version": "6.12.0"})
    assert fetched.status_code == 200
    assert fetched.json()["data"]["version"] == "6.12.0"


async def test_unknown_version_is_404(client):
    await client.post("/v1/standards/catalog", json=catalog("6.13.0"))
    missing = await client.get("/v1/standards/catalog", params={"version": "9.9.9"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


async def test_nothing_published_is_its_own_error(client):
    """Distinguishable from a bad version — it is a deployment state."""
    empty = await client.get("/v1/standards/catalog")
    assert empty.status_code == 404
    assert empty.json()["error"]["code"] == "no_catalog_published"


async def test_identical_republish_is_idempotent(client):
    """A re-run release job must not fail.

    compiled_at differs on every compile of an unchanged catalog, so it is
    excluded from the comparison — otherwise every re-run would conflict and
    the conflict would stop meaning anything.
    """
    first = await client.post("/v1/standards/catalog", json=catalog())
    assert first.json()["data"]["created"] is True

    again = await client.post(
        "/v1/standards/catalog",
        json=catalog(compiled_at="2026-09-12T04:00:00Z"),
    )
    assert again.status_code == 200
    assert again.json()["data"]["created"] is False
    assert again.json()["data"]["published_at"] == first.json()["data"]["published_at"]


async def test_changed_republish_is_a_conflict(client):
    """A published version is frozen — EVAL-002 traceability depends on it."""
    await client.post("/v1/standards/catalog", json=catalog())

    changed = await client.post(
        "/v1/standards/catalog",
        json=catalog(rule_title="Base images are pinned by tag, actually"),
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "version_exists"

    # The stored version is untouched.
    stored = await client.get("/v1/standards/catalog")
    assert stored.json()["data"]["rules"][0]["title"] == (
        "Base images are pinned by digest"
    )


async def test_rule_count_must_match_the_rules(client):
    """Guards against a truncated or hand-assembled document."""
    payload = catalog()
    payload["rule_count"] = 47
    rejected = await client.post("/v1/standards/catalog", json=payload)
    assert rejected.status_code == 422


async def test_empty_catalog_is_rejected(client):
    """A compiler that silently produced nothing must not publish."""
    payload = catalog()
    payload["rules"] = []
    payload["rule_count"] = 0
    rejected = await client.post("/v1/standards/catalog", json=payload)
    assert rejected.status_code == 422


async def test_versions_listing_carries_no_catalog_bodies(client):
    await client.post("/v1/standards/catalog", json=catalog("6.13.0"))
    await client.post("/v1/standards/catalog", json=catalog("6.9.0"))

    listed = await client.get("/v1/standards/versions")
    assert listed.status_code == 200
    data = listed.json()["data"]
    assert [row["version"] for row in data] == ["6.13.0", "6.9.0"]
    assert "rules" not in data[0]


async def test_publish_records_the_principal(client):
    published = await client.post("/v1/standards/catalog", json=catalog())
    assert published.json()["data"]["published_by"] == "dev-owner"
