"""Tests for POST /v1/secrets/rotations and the rotator enqueue seam.

The send mechanics live in services.job_queue. These pin what is the
rotation path's own: the route, its scope, the message and its queue.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mini_app_polis.api.contract import RotationJobMessage, RotationRequest
from mini_app_polis.lambda_worker import parse_message
from sqlalchemy import text

from kaianolevine_api import identity_registry
from kaianolevine_api.services import job_queue
from kaianolevine_api.services import rotation_dispatch as dispatch

pytestmark = pytest.mark.asyncio

ROUTE = "/v1/secrets/rotations"
QUEUE = "https://sqs.us-east-1.amazonaws.com/400200465748/rotator-jobs.fifo"
REQUEST = {"name": "DEEJAY_COG_API_KEY", "config": "dev", "mode": "routine"}
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "074_secret_rotation_requests.sql"
)


def _settings(monkeypatch):
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_settings.cache_clear()
    return get_settings()


async def test_a_rotation_is_enqueued_and_acknowledged(client, monkeypatch) -> None:
    _settings(monkeypatch)
    with patch.object(
        dispatch, "dispatch_rotation", AsyncMock(return_value={"message_id": "m-9"})
    ) as dispatched:
        response = await client.post(ROUTE, json=REQUEST)

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "accepted": True,
        "message_id": "m-9",
        "name": "DEEJAY_COG_API_KEY",
        "config": "dev",
    }
    (sent,) = dispatched.await_args.args
    assert sent == RotationRequest(**REQUEST)


async def test_a_dropped_rotation_is_a_502_not_a_202(client, monkeypatch) -> None:
    _settings(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_rotation",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post(ROUTE, json=REQUEST)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


@pytest.mark.parametrize(
    "body",
    [
        {**REQUEST, "name": "not a name"},
        {**REQUEST, "config": "stg"},
        {**REQUEST, "mode": "now"},
        {**REQUEST, "value": "anything"},
    ],
)
async def test_what_the_contract_refuses_is_a_422(client, body) -> None:
    with patch.object(dispatch, "dispatch_rotation", AsyncMock()) as dispatched:
        response = await client.post(ROUTE, json=body)

    assert response.status_code == 422
    dispatched.assert_not_awaited()


async def test_holding_the_record_is_not_asking_for_a_rotation(role_client) -> None:
    with patch.object(dispatch, "dispatch_rotation", AsyncMock()) as dispatched:
        async with role_client("secrets-rotator") as rotator:
            response = await rotator.post(ROUTE, json=REQUEST)

    assert response.status_code == 403
    dispatched.assert_not_awaited()


async def test_the_rotation_goes_on_the_rotators_fifo_queue(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await dispatch.dispatch_rotation(RotationRequest(**REQUEST), settings=settings)

    kwargs = sqs.send_message.call_args.kwargs
    assert kwargs["QueueUrl"] == QUEUE
    assert kwargs["MessageGroupId"] == "rotator"
    message = parse_message(kwargs["MessageBody"], RotationJobMessage)
    assert message.payload == RotationRequest(**REQUEST)
    assert json.loads(kwargs["MessageBody"])["type"] == "rotation.job"


async def test_no_machine_may_ask_for_a_rotation(client, async_engine) -> None:
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
                        "WHERE scope = 'secrets.rotations.create'"
                    )
                )
            ).all()
        }
    assert holders == {"wcs-admin"}
    machine_roles = {r for m in identity_registry.MACHINES for r in m.roles}
    assert not holders & machine_roles
