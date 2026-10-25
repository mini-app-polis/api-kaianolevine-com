"""Tests for GET /v1/secrets/key-use: a machine's last use of its previous key."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

ROUTE = "/v1/secrets/key-use"
MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "075_secret_key_use_scope.sql"
)


async def _audit(async_engine, subject: str, slot: str | None, when: str) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO identity_audit_events "
                "(enforcement_point, subject, scope, allowed, reason, key_slot, "
                "occurred_at) VALUES ('api-test', :subject, 'notify.messages.send', "
                "true, 'granted_by_role', :slot, CAST(:when AS timestamptz))"
            ),
            {"subject": subject, "slot": slot, "when": when},
        )


async def test_the_newest_previous_key_use_is_reported(client, async_engine) -> None:
    await _audit(async_engine, "deejay-cog", "previous", "2026-10-20T10:00:00Z")
    await _audit(async_engine, "deejay-cog", "previous", "2026-10-21T10:00:00Z")
    # Neither a current-key request nor another machine's counts.
    await _audit(async_engine, "deejay-cog", "current", "2026-10-22T10:00:00Z")
    await _audit(async_engine, "watcher-cog", "previous", "2026-10-23T10:00:00Z")

    response = await client.get(ROUTE, params={"machine": "deejay-cog"})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["machine"] == "deejay-cog" and data["slot"] == "previous"
    assert data["last_used"].startswith("2026-10-21T10:00:00")


async def test_a_machine_that_never_used_a_previous_key_reads_null(client) -> None:
    response = await client.get(ROUTE, params={"machine": "transcription-cog"})

    assert response.status_code == 200
    assert response.json()["data"]["last_used"] is None


@pytest.mark.parametrize("machine", ["DEEJAY_COG", "deejay cog", ""])
async def test_only_a_machine_name_is_accepted(client, machine: str) -> None:
    response = await client.get(ROUTE, params={"machine": machine})
    assert response.status_code == 422


async def test_the_rotator_may_read_it_and_others_may_not(role_client) -> None:
    async with role_client("secrets-rotator") as rotator:
        response = await rotator.get(ROUTE, params={"machine": "deejay-cog"})
        assert response.status_code == 200
    async with role_client("task-requester") as requester:
        response = await requester.get(ROUTE, params={"machine": "deejay-cog"})
        assert response.status_code == 403


async def test_the_migration_grants_the_rotator_and_the_admin(
    client, async_engine
) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM identity_role_scopes WHERE scope = 'secrets.key-use.read'"
            )
        )
    async with async_engine.connect() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(MIGRATION.read_text())  # type: ignore[union-attr]
    async with async_engine.connect() as conn:
        holders = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT role_name FROM identity_role_scopes "
                        "WHERE scope = 'secrets.key-use.read'"
                    )
                )
            ).all()
        }
    assert holders == {"secrets-rotator", "wcs-admin"}
