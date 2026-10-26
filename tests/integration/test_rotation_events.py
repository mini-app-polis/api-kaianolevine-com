"""Tests for the record of secret rotations: the upsert, the reads, the grants."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import identity_registry

pytestmark = pytest.mark.asyncio

ROUTE = "/v1/secrets/rotation-events"
MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "072_rotation_events.sql"
)

EVENT = {
    "name": "DEEJAY_COG_API_KEY",
    "config": "prd",
    "kind": "machine-key",
    "mode": "routine",
    "actor": "rotator-cog",
    "minted": "2026-10-01T09:00:00Z",
    "verification": "ok",
}


async def _record(client, **changes) -> dict:
    response = await client.post(ROUTE, json={**EVENT, **changes})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _list(client, **params) -> dict:
    response = await client.get(ROUTE, params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ── writing ───────────────────────────────────────────────────────────────


async def test_an_event_is_recorded_and_read_back(client) -> None:
    event = await _record(client)
    assert event["name"] == "DEEJAY_COG_API_KEY"
    assert event["retired"] is None
    assert event["verification"] == "ok"
    assert event["id"]
    assert event["recorded_at"]

    body = await _list(client)
    assert [e["id"] for e in body["data"]] == [event["id"]]
    assert body["meta"]["count"] == 1
    assert body["meta"]["total"] == 1


async def test_writing_the_same_key_again_updates_the_event(client) -> None:
    first = await _record(client)
    retired = await _record(client, retired="2026-10-02T09:00:00Z")

    assert retired["id"] == first["id"]
    assert retired["retired"] is not None
    assert retired["recorded_at"] >= first["recorded_at"]
    body = await _list(client)
    assert len(body["data"]) == 1
    assert body["data"][0]["retired"] is not None


async def test_a_new_mint_or_another_config_is_another_event(client) -> None:
    first = await _record(client)
    later = await _record(client, minted="2026-12-30T09:00:00Z")
    dev = await _record(client, config="dev")
    assert len({first["id"], later["id"], dev["id"]}) == 3


async def test_a_failed_verification_says_why(client) -> None:
    event = await _record(
        client,
        verification="failed",
        verification_reason="the new key was refused with a 401",
    )
    assert event["verification"] == "failed"
    assert event["verification_reason"] == "the new key was refused with a 401"


async def test_an_unchecked_rotation_is_recorded_without_a_reason(client) -> None:
    event = await _record(client, kind="manual", verification="unchecked")
    assert event["verification"] == "unchecked"
    assert event["verification_reason"] is None
    assert [e["verification"] for e in (await _list(client))["data"]] == ["unchecked"]


async def test_an_aws_key_records_only_the_last_four_of_its_id(client) -> None:
    event = await _record(
        client, name="AWS_ACCESS_KEY_ID", kind="aws-access-key", aws_key_last4="7QXZ"
    )
    assert event["aws_key_last4"] == "7QXZ"


@pytest.mark.parametrize(
    "changes",
    [
        # A field the contract does not declare is refused, so a value cannot
        # ride along under a name of its own.
        {"value": "not-a-real-secret"},
        # Names are environment variable names, never free text.
        {"name": "my secret is hunter2"},
        {"actor": "not an actor"},
        {"config": "stg"},
        {"verification_reason": "a verified rotation has no failure reason"},
        {"verification": "unchecked", "verification_reason": "no check ran"},
        {"verification": "skipped"},
        {"aws_key_last4": "7QXZ"},
        {"aws_key_last4": "7qxz-and-more", "kind": "aws-access-key"},
        {"retired": "2026-09-30T09:00:00Z"},
    ],
)
async def test_what_the_contract_refuses_is_a_422(client, changes) -> None:
    response = await client.post(ROUTE, json={**EVENT, **changes})
    assert response.status_code == 422, response.text
    assert (await _list(client))["data"] == []


async def test_the_table_refuses_what_the_contract_refuses(
    client, db_session: AsyncSession
) -> None:
    """Whoever writes the row, a column holds no free-form value."""
    row = {
        "name": "a value, not a name",
        "config": "prd",
        "kind": "manual",
        "mode": "routine",
        "actor": "rotator-cog",
        "verification": "ok",
    }
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO rotation_events "
                "(name, config, kind, mode, actor, minted, verification) "
                "VALUES (:name, :config, :kind, :mode, :actor, now(), "
                ":verification)"
            ),
            row,
        )
    await db_session.rollback()


async def test_the_table_takes_unchecked_and_refuses_any_other_verification(
    db_session: AsyncSession,
) -> None:
    insert = text(
        "INSERT INTO rotation_events "
        "(name, config, kind, mode, actor, minted, verification) "
        "VALUES (:name, 'prd', 'manual', 'routine', 'rotator-cog', "
        "now(), :verification)"
    )
    await db_session.execute(
        insert, {"name": "ACOUSTID_API_KEY", "verification": "unchecked"}
    )
    # Another secret, so only the verification can be what is refused.
    with pytest.raises(IntegrityError):
        await db_session.execute(
            insert, {"name": "BREVO_API_KEY", "verification": "skipped"}
        )
    await db_session.rollback()


# ── reading ───────────────────────────────────────────────────────────────


async def test_events_come_newest_minted_first_and_narrow_by_name_and_config(
    client,
) -> None:
    await _record(client, minted="2026-01-01T09:00:00Z")
    await _record(client, minted="2026-04-01T09:00:00Z")
    await _record(client, config="dev", minted="2026-03-01T09:00:00Z")
    await _record(client, name="BREVO_API_KEY", kind="manual")

    every = (await _list(client))["data"]
    assert [e["minted"][:10] for e in every] == [
        "2026-10-01",
        "2026-04-01",
        "2026-03-01",
        "2026-01-01",
    ]

    narrowed = await _list(client, name="DEEJAY_COG_API_KEY", config="prd")
    deejay_prd = narrowed["data"]
    assert [e["minted"][:10] for e in deejay_prd] == ["2026-04-01", "2026-01-01"]
    assert (await _list(client, name="NOT_ROTATED"))["data"] == []


async def test_latest_is_the_newest_event_per_name_and_config(client) -> None:
    await _record(client, minted="2026-01-01T09:00:00Z")
    newest_prd = await _record(client, minted="2026-04-01T09:00:00Z")
    newest_dev = await _record(client, config="dev", minted="2026-03-01T09:00:00Z")
    await _record(client, config="dev", minted="2026-02-01T09:00:00Z")
    brevo = await _record(client, name="BREVO_API_KEY", kind="manual")

    body = await _list(client, latest="true")
    assert [e["id"] for e in body["data"]] == [
        brevo["id"],
        newest_prd["id"],
        newest_dev["id"],
    ]
    assert body["meta"]["total"] == 3

    dev_only = (await _list(client, latest="true", config="dev"))["data"]
    assert [e["id"] for e in dev_only] == [newest_dev["id"]]


async def test_limit_and_offset_page_through_and_total_counts_every_match(
    client,
) -> None:
    for month in ("01", "02", "03"):
        await _record(client, minted=f"2026-{month}-01T09:00:00Z")

    page = await _list(client, limit=2, offset=1)
    assert [e["minted"][:10] for e in page["data"]] == ["2026-02-01", "2026-01-01"]
    assert page["meta"]["count"] == 2
    assert page["meta"]["total"] == 3
    assert (await client.get(ROUTE, params={"limit": 501})).status_code == 422


# ── who may do what ───────────────────────────────────────────────────────


async def test_the_rotator_role_may_write_and_read(client, role_client) -> None:
    async with role_client("secrets-rotator") as rotator:
        assert (await rotator.post(ROUTE, json=EVENT)).status_code == 200
        assert (await rotator.get(ROUTE, params={"latest": "true"})).status_code == 200
        # Writing the record is not asking for a rotation.
        assert (await rotator.post("/v1/tasks/sweeps")).status_code == 403


async def test_a_caller_without_the_scopes_is_refused(client, role_client) -> None:
    async with role_client("task-requester") as requester:
        assert (await requester.post(ROUTE, json=EVENT)).status_code == 403
        assert (await requester.get(ROUTE)).status_code == 403


async def test_the_migration_grants_the_rotator_and_the_admin(
    client, async_engine
) -> None:
    """The owner's hand-run rotations write under his own identity."""
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM identity_role_scopes "
                "WHERE scope LIKE 'secrets.rotation-events.%'"
            )
        )
    async with async_engine.connect() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(MIGRATION.read_text())  # type: ignore[union-attr]
    async with async_engine.connect() as conn:
        granted = set(
            (
                await conn.execute(
                    text(
                        "SELECT role_name, scope FROM identity_role_scopes "
                        "WHERE scope LIKE 'secrets.rotation-events.%'"
                    )
                )
            ).all()
        )
    assert granted == {
        ("secrets-rotator", "secrets.rotation-events.read"),
        ("secrets-rotator", "secrets.rotation-events.write"),
        ("wcs-admin", "secrets.rotation-events.read"),
        ("wcs-admin", "secrets.rotation-events.write"),
    }


async def test_the_rotator_machine_records_rotations_and_asks_for_tasks() -> None:
    rotator = identity_registry.declared("rotator-cog")
    assert rotator is not None
    assert set(rotator.roles) == {"secrets-rotator", "task-requester", "notifier"}
    assert rotator.key_env_var == "ROTATOR_COG_API_KEY"
