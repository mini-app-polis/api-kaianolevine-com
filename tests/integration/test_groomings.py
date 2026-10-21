"""Tests for the task board's grooming records and the month's paid grooming."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import identity_registry

pytestmark = pytest.mark.asyncio

RECORD = {
    "task_gid": "1219755103049631",
    "task_id": "MINI-151",
    "fingerprint": "a" * 64,
    "outcome": "not_ready",
    "problems": ["Effort is blank, not S, M or L"],
}


async def test_a_record_is_written_and_listed(client) -> None:
    written = await client.post("/v1/tasks/groomings", json=RECORD)
    assert written.status_code == 200, written.text
    stored = written.json()["data"]
    assert stored["problems"] == RECORD["problems"]
    assert stored["returned_count"] == 0
    assert stored["moved_to_ready_at"] is None

    listed = await client.get("/v1/tasks/groomings")
    assert listed.status_code == 200
    assert [r["task_gid"] for r in listed.json()["data"]] == [RECORD["task_gid"]]


async def test_writing_a_record_again_replaces_it(client) -> None:
    await client.post("/v1/tasks/groomings", json=RECORD)
    moved = {
        **RECORD,
        "fingerprint": "b" * 64,
        "outcome": "ready",
        "problems": [],
        "moved_to_ready_at": "2026-10-21T20:00:00Z",
        "returned_count": 1,
    }
    response = await client.post("/v1/tasks/groomings", json=moved)
    data = response.json()["data"]
    assert data["outcome"] == "ready"
    assert data["fingerprint"] == "b" * 64
    assert data["returned_count"] == 1
    assert data["moved_to_ready_at"].startswith("2026-10-21T20:00:00")

    listed = (await client.get("/v1/tasks/groomings")).json()["data"]
    assert len(listed) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"fingerprint": "not-a-sha"},
        {"outcome": "maybe"},
        {"task_gid": "MINI-151"},
        {"returned_count": -1},
        {"extra": True},
    ],
)
async def test_a_malformed_record_is_rejected(client, change) -> None:
    response = await client.post("/v1/tasks/groomings", json={**RECORD, **change})
    assert response.status_code == 422


async def test_paid_runs_add_up_to_the_months_spend(
    client, db_session: AsyncSession
) -> None:
    empty = (await client.get("/v1/tasks/groomings/spend")).json()["data"]
    assert empty["runs"] == 0
    assert empty["cost_usd"] == 0

    for cost in (0.12, 0.3):
        response = await client.post(
            "/v1/tasks/groomings/runs",
            json={
                "task_gid": RECORD["task_gid"],
                "model": "claude-sonnet-5-5",
                "input_tokens": 12000,
                "output_tokens": 800,
                "cost_usd": cost,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["data"]["cost_usd"] == cost

    # A run from last month is not this month's spend.
    await db_session.execute(
        text(
            "INSERT INTO task_grooming_runs "
            "(task_gid, model, input_tokens, output_tokens, cost_usd, recorded_at) "
            "VALUES ('1', 'm', 1, 1, 5, now() - interval '40 days')"
        )
    )
    await db_session.commit()

    spend = (await client.get("/v1/tasks/groomings/spend")).json()["data"]
    assert spend["runs"] == 2
    assert spend["cost_usd"] == pytest.approx(0.42)
    assert len(spend["month"]) == 7


async def test_the_task_manager_keeps_the_records() -> None:
    cog = identity_registry.declared("task-manager-cog")
    assert cog is not None
    assert "task-groomer" in cog.roles
