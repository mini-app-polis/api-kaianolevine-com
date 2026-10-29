"""Rotation incidents: starting or resuming one, the rotator's registration
and task marks, the incident filter on events, and the read.

No test sends anything to a real queue: the dispatch seam is patched, or
boto3 is.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mini_app_polis.api.contract import RotationIncidentMessage
from mini_app_polis.lambda_worker import parse_message
from sqlalchemy import text

from kaianolevine_api import identity_registry
from kaianolevine_api.services import job_queue, task_dispatch
from kaianolevine_api.services import rotation_dispatch as dispatch

pytestmark = pytest.mark.asyncio

START = "/v1/secrets/rotation-incidents"
EVENTS = "/v1/secrets/rotation-events"
QUEUE = "https://sqs.us-east-1.amazonaws.com/400200465748/rotator-jobs.fifo"
UNKNOWN = "00000000-0000-4000-8000-000000000000"
MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "082_rotation_incidents.sql"
)


def _settings(monkeypatch):
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_settings.cache_clear()
    return get_settings()


def _entry(
    name: str, config: str = "prd", tier: int = 1, handling: str = "worker"
) -> dict:
    return {"name": name, "config": config, "tier": tier, "handling": handling}


def _event(name: str, config: str, minted: datetime, **overrides: Any) -> dict:
    return {
        "name": name,
        "config": config,
        "kind": "machine-key",
        "mode": "compromised",
        "actor": "rotator-cog",
        "minted": minted.isoformat(),
        "verification": "ok",
        **overrides,
    }


async def _start(client, body: dict | None = None) -> dict:
    with patch.object(
        dispatch, "dispatch_incident", AsyncMock(return_value={"message_id": "m-1"})
    ):
        response = await client.post(START, json=body or {"mode": "compromised"})
    assert response.status_code == 202, response.text
    return response.json()["data"]


async def _register(rotator, incident_id: str, entries: list[dict]) -> Any:
    return await rotator.post(
        f"{START}/{incident_id}/entries", json={"entries": entries}
    )


# ── Starting and resuming ────────────────────────────────────────────────


async def test_an_incident_is_minted_recorded_and_queued(
    client, db_session, monkeypatch
) -> None:
    _settings(monkeypatch)
    with patch.object(
        dispatch, "dispatch_incident", AsyncMock(return_value={"message_id": "m-9"})
    ) as dispatched:
        response = await client.post(START, json={"mode": "compromised"})

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert set(data) == {
        "accepted",
        "message_id",
        "incident_id",
        "started_at",
        "resumed",
    }
    assert data["accepted"] is True
    assert data["message_id"] == "m-9"
    assert data["resumed"] is False
    assert dispatched.await_args.args == (data["incident_id"],)
    stored = (
        await db_session.execute(
            text(
                "SELECT started_at FROM rotation_incidents WHERE id = CAST(:id AS UUID)"
            ),
            {"id": data["incident_id"]},
        )
    ).scalar_one()
    assert stored == datetime.fromisoformat(data["started_at"])


async def test_asking_again_without_an_id_is_a_new_incident(client) -> None:
    first = await _start(client)
    second = await _start(client)
    assert first["incident_id"] != second["incident_id"]


async def test_asking_with_an_id_resumes_that_incident(client) -> None:
    first = await _start(client)
    with patch.object(
        dispatch, "dispatch_incident", AsyncMock(return_value={"message_id": "m-2"})
    ) as dispatched:
        response = await client.post(
            START, json={"mode": "compromised", "incident_id": first["incident_id"]}
        )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["incident_id"] == first["incident_id"]
    assert data["started_at"] == first["started_at"]
    assert data["resumed"] is True
    assert dispatched.await_args.args == (first["incident_id"],)


async def test_resuming_an_unknown_incident_is_a_404(client) -> None:
    with patch.object(dispatch, "dispatch_incident", AsyncMock()) as dispatched:
        response = await client.post(
            START, json={"mode": "compromised", "incident_id": UNKNOWN}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    dispatched.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"mode": "routine"},
        {"mode": "compromised", "incident_id": "incident-1"},
        {"mode": "compromised", "name": "TICK_SECRET"},
        {"mode": "compromised", "value": "anything"},
    ],
)
async def test_what_the_contract_refuses_is_a_422(client, body) -> None:
    with patch.object(dispatch, "dispatch_incident", AsyncMock()) as dispatched:
        response = await client.post(START, json=body)

    assert response.status_code == 422
    dispatched.assert_not_awaited()


async def test_a_dropped_incident_is_a_502_naming_it_and_can_be_resumed(
    client, monkeypatch
) -> None:
    _settings(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_incident",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post(START, json={"mode": "compromised"})

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "dispatch_failed"
    (incident_id,) = (
        word.strip(".:")
        for word in error["message"].split()
        if len(word.strip(".:")) == 36
    )
    resumed = await _start(client, {"mode": "compromised", "incident_id": incident_id})
    assert resumed["resumed"] is True


async def test_only_the_admin_starts_an_incident(role_client) -> None:
    with patch.object(dispatch, "dispatch_incident", AsyncMock()) as dispatched:
        async with role_client("secrets-rotator") as rotator:
            response = await rotator.post(START, json={"mode": "compromised"})

    assert response.status_code == 403
    dispatched.assert_not_awaited()


async def test_the_incident_goes_on_the_rotators_fifo_queue(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    incident_id = "7c2d4e1a-9b3f-4a6c-8d5e-1f2a3b4c5d6e"
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await dispatch.dispatch_incident(incident_id, settings=settings)

    kwargs = sqs.send_message.call_args.kwargs
    assert kwargs["QueueUrl"] == QUEUE
    assert kwargs["MessageGroupId"] == "rotator"
    message = parse_message(kwargs["MessageBody"], RotationIncidentMessage)
    assert message.payload.incident_id == incident_id
    assert json.loads(kwargs["MessageBody"]) == {
        "type": "rotation.incident",
        "version": 1,
        "payload": {"incident_id": incident_id},
    }


# ── Registration and task marks ──────────────────────────────────────────


async def test_the_rotator_registers_the_entries_in_order(client, role_client) -> None:
    incident = await _start(client)
    entries = [
        _entry("DEEJAY_COG_API_KEY", tier=0),
        _entry("OPENAI_API_KEY", tier=2, handling="task"),
        _entry("DEEJAY_COG_API_KEY", config="dev", tier=0),
    ]
    async with role_client("secrets-rotator") as rotator:
        response = await _register(rotator, incident["incident_id"], entries)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["incident_id"] == incident["incident_id"]
    assert [(e["name"], e["config"]) for e in data["entries"]] == [
        ("DEEJAY_COG_API_KEY", "prd"),
        ("OPENAI_API_KEY", "prd"),
        ("DEEJAY_COG_API_KEY", "dev"),
    ]
    assert {e["state"] for e in data["entries"]} == {"outstanding"}
    assert all(e["task_requested"] is False for e in data["entries"])


async def test_registering_again_keeps_the_task_flag_and_drops_what_is_gone(
    client, role_client
) -> None:
    incident_id = (await _start(client))["incident_id"]
    async with role_client("secrets-rotator") as rotator:
        await _register(
            rotator,
            incident_id,
            [_entry("A_KEY", handling="task"), _entry("B_KEY"), _entry("GONE_KEY")],
        )
        marked = await rotator.post(
            f"{START}/{incident_id}/task-requested",
            json={"name": "A_KEY", "config": "prd"},
        )
        assert marked.status_code == 200, marked.text
        again = await _register(
            rotator,
            incident_id,
            [_entry("B_KEY", tier=3), _entry("A_KEY", handling="task")],
        )

    data = again.json()["data"]
    assert [(e["name"], e["tier"], e["task_requested"]) for e in data["entries"]] == [
        ("B_KEY", 3, False),
        ("A_KEY", 1, True),
    ]


async def test_registering_an_unknown_incident_is_a_404(role_client) -> None:
    async with role_client("secrets-rotator") as rotator:
        response = await _register(rotator, UNKNOWN, [_entry("A_KEY")])
    assert response.status_code == 404


async def test_marking_a_task_sets_task_opened(client, role_client) -> None:
    incident_id = (await _start(client))["incident_id"]
    async with role_client("secrets-rotator") as rotator:
        await _register(
            rotator, incident_id, [_entry("A_KEY", config="dev", handling="task")]
        )
        first = await rotator.post(
            f"{START}/{incident_id}/task-requested",
            json={"name": "A_KEY", "config": "dev"},
        )
        repeat = await rotator.post(
            f"{START}/{incident_id}/task-requested",
            json={"name": "A_KEY", "config": "dev"},
        )

    assert first.status_code == 200, first.text
    assert first.json()["data"] == {
        "name": "A_KEY",
        "config": "dev",
        "tier": 1,
        "handling": "task",
        "task_requested": True,
        "state": "task-opened",
    }
    assert repeat.json()["data"] == first.json()["data"]


async def test_marking_an_entry_not_registered_is_a_404(client, role_client) -> None:
    incident_id = (await _start(client))["incident_id"]
    async with role_client("secrets-rotator") as rotator:
        await _register(rotator, incident_id, [_entry("A_KEY")])
        other_config = await rotator.post(
            f"{START}/{incident_id}/task-requested",
            json={"name": "A_KEY", "config": "dev"},
        )
        unknown = await rotator.post(
            f"{START}/{UNKNOWN}/task-requested", json={"name": "A_KEY", "config": "prd"}
        )
    assert other_config.status_code == 404
    assert unknown.status_code == 404


async def test_an_entry_shaped_like_a_value_is_refused(client, role_client) -> None:
    incident_id = (await _start(client))["incident_id"]
    async with role_client("secrets-rotator") as rotator:
        for entry in (
            _entry("sk_live_abc123"),
            {**_entry("A_KEY"), "value": "secret"},
            _entry("A_KEY", tier=4),
        ):
            response = await _register(rotator, incident_id, [entry])
            assert response.status_code == 422, entry


async def test_the_incident_routes_need_their_scopes(client, role_client) -> None:
    incident_id = (await _start(client))["incident_id"]
    async with role_client("notifier") as stranger:
        assert (await stranger.get(f"{START}/{incident_id}")).status_code == 403
        assert (
            await _register(stranger, incident_id, [_entry("A_KEY")])
        ).status_code == 403
        assert (
            await stranger.post(
                f"{START}/{incident_id}/task-requested",
                json={"name": "A_KEY", "config": "prd"},
            )
        ).status_code == 403


async def test_an_id_that_is_not_an_incident_id_is_a_422(client) -> None:
    assert (await client.get(f"{START}/not-a-uuid")).status_code == 422


# ── Events carry their incident ──────────────────────────────────────────


async def test_an_event_records_its_incident_and_the_list_filters_by_it(
    client,
) -> None:
    incident_id = (await _start(client))["incident_id"]
    minted = datetime.now(UTC) + timedelta(seconds=5)
    tagged = await client.post(
        EVENTS, json=_event("A_KEY", "prd", minted, incident_id=incident_id)
    )
    assert tagged.status_code == 200, tagged.text
    assert tagged.json()["data"]["incident_id"] == incident_id
    await client.post(EVENTS, json=_event("B_KEY", "prd", minted))
    # Writing the same key again without the incident keeps it.
    retired = await client.post(
        EVENTS,
        json=_event(
            "A_KEY", "prd", minted, retired=(minted + timedelta(seconds=1)).isoformat()
        ),
    )
    assert retired.json()["data"]["incident_id"] == incident_id

    listed = await client.get(EVENTS, params={"incident": incident_id})
    assert listed.status_code == 200
    assert [e["name"] for e in listed.json()["data"]] == ["A_KEY"]
    assert listed.json()["meta"]["total"] == 1
    everything = await client.get(EVENTS)
    assert {e["name"] for e in everything.json()["data"]} >= {"A_KEY", "B_KEY"}
    assert (await client.get(EVENTS, params={"incident": "x"})).status_code == 422


# ── The read ─────────────────────────────────────────────────────────────


async def test_each_entry_state_and_the_outstanding_order(
    client, role_client, db_session
) -> None:
    incident_id = (await _start(client))["incident_id"]
    started = (
        await db_session.execute(
            text(
                "SELECT started_at FROM rotation_incidents WHERE id = CAST(:id AS UUID)"
            ),
            {"id": incident_id},
        )
    ).scalar_one()
    before = started - timedelta(hours=1)
    after = started + timedelta(seconds=10)
    async with role_client("secrets-rotator") as rotator:
        await _register(
            rotator,
            incident_id,
            [
                _entry("ROTATED_KEY", tier=0),
                _entry("UNCHECKED_KEY", tier=0, handling="task"),
                _entry("OLD_ONLY_KEY", tier=0),
                _entry("FAILED_KEY", config="dev", tier=2),
                _entry("RETRIED_KEY", tier=1),
                _entry("TASKED_KEY", config="dev", tier=0, handling="task"),
                _entry("FAILED_TASKED_KEY", tier=3, handling="task"),
                _entry("WAITING_KEY", tier=1),
            ],
        )
        for name, config in (("TASKED_KEY", "dev"), ("FAILED_TASKED_KEY", "prd")):
            await rotator.post(
                f"{START}/{incident_id}/task-requested",
                json={"name": name, "config": config},
            )

    failed = {"verification": "failed", "verification_reason": "whoami answered 401"}
    for event in (
        # Not tagged with the incident: any rotation after the start counts.
        _event("ROTATED_KEY", "prd", after),
        _event(
            "UNCHECKED_KEY",
            "prd",
            after,
            kind="manual",
            actor="owner@example.com",
            verification="unchecked",
        ),
        # Before the incident started: does not count.
        _event("OLD_ONLY_KEY", "prd", before),
        _event("FAILED_KEY", "dev", after, incident_id=incident_id, **failed),
        # A failure then a success: rotated.
        _event("RETRIED_KEY", "prd", after, **failed),
        _event("RETRIED_KEY", "prd", after + timedelta(seconds=1)),
        _event("FAILED_TASKED_KEY", "prd", after, **failed),
    ):
        response = await client.post(EVENTS, json=event)
        assert response.status_code == 200, response.text

    with (
        patch.object(
            job_queue, "enqueue", AsyncMock(side_effect=AssertionError("queue"))
        ),
        patch.object(
            task_dispatch,
            "dispatch_task",
            AsyncMock(side_effect=AssertionError("board")),
            create=True,
        ),
    ):
        response = await client.get(f"{START}/{incident_id}")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["started_at"] is not None
    states = {e["name"]: e["state"] for e in data["entries"]}
    assert states == {
        "ROTATED_KEY": "rotated",
        "UNCHECKED_KEY": "rotated",
        "OLD_ONLY_KEY": "outstanding",
        "FAILED_KEY": "failed",
        "RETRIED_KEY": "rotated",
        "TASKED_KEY": "task-opened",
        "FAILED_TASKED_KEY": "failed",
        "WAITING_KEY": "outstanding",
    }
    # Failed first, then prd before dev, then by tier, then as registered.
    assert [e["name"] for e in data["outstanding"]] == [
        "FAILED_TASKED_KEY",
        "FAILED_KEY",
        "OLD_ONLY_KEY",
        "WAITING_KEY",
        "TASKED_KEY",
    ]


async def test_an_incident_not_registered_yet_reads_empty(client) -> None:
    incident_id = (await _start(client))["incident_id"]
    response = await client.get(f"{START}/{incident_id}")
    assert response.status_code == 200
    assert response.json()["data"]["entries"] == []
    assert response.json()["data"]["outstanding"] == []


async def test_reading_an_unknown_incident_is_a_404(client) -> None:
    assert (await client.get(f"{START}/{UNKNOWN}")).status_code == 404


async def test_no_answer_or_log_carries_more_than_names(
    client, role_client, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    incident_id = (await _start(client))["incident_id"]
    async with role_client("secrets-rotator") as rotator:
        registered = await _register(rotator, incident_id, [_entry("A_KEY")])
        read = await rotator.get(f"{START}/{incident_id}")
    for body in (registered.json()["data"], read.json()["data"]):
        assert set(body) == {"incident_id", "started_at", "entries", "outstanding"}
        for entry in body["entries"]:
            assert set(entry) == {
                "name",
                "config",
                "tier",
                "handling",
                "task_requested",
                "state",
            }
    assert "sk_" not in caplog.text


# ── Who holds what ───────────────────────────────────────────────────────


async def test_the_migration_grants_the_rotator_and_the_admin(
    client, async_engine
) -> None:
    # The client seeds the roles; the migration's own grants are what is read.
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM identity_role_scopes "
                "WHERE scope LIKE 'secrets.rotation-incidents.%'"
            )
        )
    async with async_engine.connect() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(MIGRATION.read_text())  # type: ignore[union-attr]
    async with async_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT role_name, scope FROM identity_role_scopes "
                    "WHERE scope LIKE 'secrets.rotation-incidents.%'"
                )
            )
        ).all()
    assert {(r[0], r[1]) for r in rows} == {
        ("secrets-rotator", "secrets.rotation-incidents.write"),
        ("secrets-rotator", "secrets.rotation-incidents.read"),
        ("wcs-admin", "secrets.rotation-incidents.read"),
    }
    rotator = next(m for m in identity_registry.MACHINES if m.name == "rotator-cog")
    assert "secrets-rotator" in rotator.roles
