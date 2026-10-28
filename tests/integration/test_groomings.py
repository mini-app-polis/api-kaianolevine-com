"""Tests for the task board's grooming records, paid grooming, and the hand-offs."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mini_app_polis.api.contract import TaskGroomMessage, TaskGroomProposalMessage
from mini_app_polis.lambda_worker import parse_message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import identity_registry
from kaianolevine_api.schemas import GroomingJobRequest, GroomingProposal
from kaianolevine_api.services import grooming_dispatch, job_queue

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
        "groomed_fingerprint": "b" * 64,
    }
    response = await client.post("/v1/tasks/groomings", json=moved)
    data = response.json()["data"]
    assert data["outcome"] == "ready"
    assert data["fingerprint"] == "b" * 64
    assert data["returned_count"] == 1
    assert data["groomed_fingerprint"] == "b" * 64
    assert data["moved_to_ready_at"].startswith("2026-10-21T20:00:00")

    listed = (await client.get("/v1/tasks/groomings")).json()["data"]
    assert len(listed) == 1


async def test_a_record_keeps_a_send_until_a_grooming_clears_it(client) -> None:
    fresh = (await client.post("/v1/tasks/groomings", json=RECORD)).json()["data"]
    assert (fresh["groom_sent_at"], fresh["groom_attempts"]) == (None, 0)

    sent = {
        **RECORD,
        "groomed_fingerprint": RECORD["fingerprint"],
        "groom_sent_at": "2026-10-28T09:15:00Z",
        "groom_attempts": 2,
    }
    data = (await client.post("/v1/tasks/groomings", json=sent)).json()["data"]
    assert data["groom_sent_at"].startswith("2026-10-28T09:15:00")
    assert data["groom_attempts"] == 2
    listed = (await client.get("/v1/tasks/groomings")).json()["data"][0]
    assert listed["groom_attempts"] == 2

    groomed = {**RECORD, "groomed_fingerprint": "b" * 64}
    data = (await client.post("/v1/tasks/groomings", json=groomed)).json()["data"]
    assert (data["groom_sent_at"], data["groom_attempts"]) == (None, 0)


@pytest.mark.parametrize(
    "change",
    [
        {"fingerprint": "not-a-sha"},
        {"outcome": "maybe"},
        {"task_gid": "MINI-151"},
        {"returned_count": -1},
        {"groom_attempts": -1},
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


# ── hand-offs between the task manager and the groomer ────────────────────

ACCOUNT_QUEUES = "https://sqs.us-east-1.amazonaws.com/400200465748"

JOB = {
    "task": {
        "task_gid": "1219755103049631",
        "task_id": "MINI-38",
        "name": "MINI-38 · idea - dashboard for TV shows and movies",
        "notes": "Done when:\nWhere:\n",
        "tags": [],
        "section": "Ideas",
        "fingerprint": "c" * 64,
        "problems": ["Done when is blank"],
    },
    "board": ["MINI-9 · Discord commands and actions"],
}

PROPOSAL = {
    "task_gid": "1219755103049631",
    "fingerprint": "c" * 64,
    "title": "Dashboard for TV shows and movies",
    "where": "website-astro-software",
    "effort": "M",
    "questions": ["Which service holds the watch history?"],
    "model": "claude-sonnet-5-5",
    "cost_usd": 0.11,
}


def _aws(monkeypatch):
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_settings.cache_clear()
    return get_settings()


async def test_a_grooming_job_is_enqueued_and_acknowledged(client, monkeypatch) -> None:
    _aws(monkeypatch)
    with patch.object(
        grooming_dispatch, "dispatch_job", AsyncMock(return_value={"message_id": "m-1"})
    ) as dispatched:
        response = await client.post("/v1/tasks/groomings/jobs", json=JOB)

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "accepted": True,
        "message_id": "m-1",
        "task_gid": JOB["task"]["task_gid"],
    }
    assert dispatched.await_args.args[0] == GroomingJobRequest.model_validate(JOB)


async def test_a_proposal_is_enqueued_for_the_task_manager(client, monkeypatch) -> None:
    _aws(monkeypatch)
    with patch.object(
        grooming_dispatch,
        "dispatch_proposal",
        AsyncMock(return_value={"message_id": "m-2"}),
    ) as dispatched:
        response = await client.post("/v1/tasks/groomings/proposals", json=PROPOSAL)

    assert response.status_code == 202, response.text
    assert response.json()["data"]["message_id"] == "m-2"
    assert dispatched.await_args.args[0] == GroomingProposal.model_validate(PROPOSAL)


@pytest.mark.parametrize(
    ("path", "body", "target"),
    [
        ("/v1/tasks/groomings/jobs", JOB, "dispatch_job"),
        ("/v1/tasks/groomings/proposals", PROPOSAL, "dispatch_proposal"),
    ],
)
async def test_a_dropped_hand_off_is_a_502(client, monkeypatch, path, body, target):
    _aws(monkeypatch)
    with patch.object(
        grooming_dispatch,
        target,
        AsyncMock(side_effect=grooming_dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post(path, json=body)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


async def test_a_proposal_with_an_effort_off_the_scale_is_rejected(client) -> None:
    response = await client.post(
        "/v1/tasks/groomings/proposals", json={**PROPOSAL, "effort": "XL"}
    )
    assert response.status_code == 422


async def test_a_job_goes_to_the_groomers_queue(monkeypatch) -> None:
    settings = _aws(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await grooming_dispatch.dispatch_job(
            GroomingJobRequest.model_validate(JOB), settings=settings
        )
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == f"{ACCOUNT_QUEUES}/task-groomer-jobs"
    assert "MessageGroupId" not in sent
    body = json.loads(sent["MessageBody"])
    assert (body["type"], body["version"]) == ("task.groom", 1)
    # What task-groomer-cog's worker reads it as.
    assert parse_message(sent["MessageBody"], TaskGroomMessage).payload == (
        GroomingJobRequest.model_validate(JOB)
    )
    assert GroomingJobRequest.model_validate(body["payload"]) == (
        GroomingJobRequest.model_validate(JOB)
    )


async def test_a_proposal_goes_to_the_task_managers_queue(monkeypatch) -> None:
    settings = _aws(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-2"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await grooming_dispatch.dispatch_proposal(
            GroomingProposal.model_validate(PROPOSAL), settings=settings
        )
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == f"{ACCOUNT_QUEUES}/task-manager-jobs.fifo"
    assert sent["MessageGroupId"] == "task-manager"
    body = json.loads(sent["MessageBody"])
    assert (body["type"], body["version"]) == ("task.groom-proposal", 1)
    # What task-manager-cog's worker reads it as.
    assert parse_message(sent["MessageBody"], TaskGroomProposalMessage).payload == (
        GroomingProposal.model_validate(PROPOSAL)
    )


async def test_the_groomer_proposes_and_never_asks_for_tasks() -> None:
    cog = identity_registry.declared("task-groomer-cog")
    assert cog is not None
    assert "task-groomer" in cog.roles
    assert "task-requester" not in cog.roles


# ── claims on grooming jobs ───────────────────────────────────────────────

CLAIM = {"task_gid": "1219848148842653", "fingerprint": "c" * 64}


async def test_the_first_claim_wins_and_a_repeat_does_not(client) -> None:
    first = await client.post("/v1/tasks/groomings/claims", json=CLAIM)
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {**CLAIM, "claimed": True}

    again = await client.post("/v1/tasks/groomings/claims", json=CLAIM)
    assert again.status_code == 200
    assert again.json()["data"]["claimed"] is False


async def test_two_concurrent_claims_have_one_winner(client) -> None:
    results = await asyncio.gather(
        *(client.post("/v1/tasks/groomings/claims", json=CLAIM) for _ in range(5))
    )
    assert all(r.status_code == 200 for r in results)
    assert sorted(r.json()["data"]["claimed"] for r in results) == [
        False,
        False,
        False,
        False,
        True,
    ]


async def test_a_released_claim_can_be_claimed_again(client) -> None:
    await client.post("/v1/tasks/groomings/claims", json=CLAIM)

    released = await client.post("/v1/tasks/groomings/claims/release", json=CLAIM)
    assert released.status_code == 200, released.text
    assert released.json()["data"] == {**CLAIM, "released": True}

    again = await client.post("/v1/tasks/groomings/claims", json=CLAIM)
    assert again.json()["data"]["claimed"] is True
    held = await client.post("/v1/tasks/groomings/claims", json=CLAIM)
    assert held.json()["data"]["claimed"] is False


async def test_releasing_what_is_not_held_releases_nothing(client) -> None:
    never = await client.post("/v1/tasks/groomings/claims/release", json=CLAIM)
    assert never.status_code == 200
    assert never.json()["data"]["released"] is False

    await client.post("/v1/tasks/groomings/claims", json=CLAIM)
    await client.post("/v1/tasks/groomings/claims/release", json=CLAIM)
    twice = await client.post("/v1/tasks/groomings/claims/release", json=CLAIM)
    assert twice.json()["data"]["released"] is False


async def test_new_content_for_the_same_task_is_a_new_claim(client) -> None:
    await client.post("/v1/tasks/groomings/claims", json=CLAIM)
    changed = {**CLAIM, "fingerprint": "d" * 64}
    response = await client.post("/v1/tasks/groomings/claims", json=changed)
    assert response.json()["data"]["claimed"] is True


@pytest.mark.parametrize(
    "change",
    [{"fingerprint": "not-a-sha"}, {"task_gid": "MINI-292"}, {"extra": True}],
)
async def test_a_malformed_claim_is_rejected(client, change) -> None:
    for path in ("/v1/tasks/groomings/claims", "/v1/tasks/groomings/claims/release"):
        response = await client.post(path, json={**CLAIM, **change})
        assert response.status_code == 422


async def test_a_caller_without_the_scope_may_not_claim_or_release(
    client, role_client
) -> None:
    async with role_client("agent-reporter") as reporter:
        for path in (
            "/v1/tasks/groomings/claims",
            "/v1/tasks/groomings/claims/release",
        ):
            assert (await reporter.post(path, json=CLAIM)).status_code == 403
    async with role_client("task-groomer") as groomer:
        response = await groomer.post("/v1/tasks/groomings/claims", json=CLAIM)
        assert response.json()["data"]["claimed"] is True


async def test_the_groomer_holds_the_claim_scope() -> None:
    cog = identity_registry.declared("task-groomer-cog")
    assert cog is not None
    assert "task-groomer" in cog.roles


# ── runs the spent budget stopped ─────────────────────────────────────────

STOP = {"task_gid": RECORD["task_gid"], "fingerprint": RECORD["fingerprint"]}


async def _listed_stop(client, task_gid: str) -> str | None:
    listed = (await client.get("/v1/tasks/groomings")).json()["data"]
    (record,) = (r for r in listed if r["task_gid"] == task_gid)
    return record["budget_spent_fingerprint"]


async def test_a_stop_is_marked_and_served_on_the_record(client) -> None:
    await client.post("/v1/tasks/groomings", json=RECORD)
    assert await _listed_stop(client, RECORD["task_gid"]) is None

    marked = await client.post("/v1/tasks/groomings/budget-spent", json=STOP)
    assert marked.status_code == 200, marked.text
    assert marked.json()["data"] == STOP
    assert await _listed_stop(client, RECORD["task_gid"]) == STOP["fingerprint"]


async def test_a_later_stop_replaces_the_tasks_last(client) -> None:
    await client.post("/v1/tasks/groomings", json=RECORD)
    await client.post("/v1/tasks/groomings/budget-spent", json=STOP)
    later = {**STOP, "fingerprint": "e" * 64}
    await client.post("/v1/tasks/groomings/budget-spent", json=later)
    assert await _listed_stop(client, RECORD["task_gid"]) == "e" * 64


async def test_a_stop_is_marked_before_the_task_has_a_record(client) -> None:
    marked = await client.post("/v1/tasks/groomings/budget-spent", json=STOP)
    assert marked.status_code == 200, marked.text
    assert (await client.get("/v1/tasks/groomings")).json()["data"] == []

    # The sweep's first record for the task is served with it.
    written = await client.post("/v1/tasks/groomings", json=RECORD)
    assert written.json()["data"]["budget_spent_fingerprint"] == STOP["fingerprint"]
    assert await _listed_stop(client, RECORD["task_gid"]) == STOP["fingerprint"]


async def test_a_won_claim_for_the_stopped_content_clears_the_stop(client) -> None:
    await client.post("/v1/tasks/groomings", json=RECORD)
    await client.post("/v1/tasks/groomings/budget-spent", json=STOP)

    won = await client.post("/v1/tasks/groomings/claims", json=STOP)
    assert won.json()["data"] == {**STOP, "claimed": True}
    assert await _listed_stop(client, RECORD["task_gid"]) is None


async def test_a_claim_that_does_not_clear_the_stop(client) -> None:
    await client.post("/v1/tasks/groomings", json=RECORD)
    other = {**STOP, "fingerprint": "f" * 64}

    # A claim held before the stop is not won again, so the stop stays.
    await client.post("/v1/tasks/groomings/claims", json=STOP)
    await client.post("/v1/tasks/groomings/budget-spent", json=STOP)
    held = await client.post("/v1/tasks/groomings/claims", json=STOP)
    assert held.json()["data"]["claimed"] is False
    assert await _listed_stop(client, RECORD["task_gid"]) == STOP["fingerprint"]

    # A claim won for other content leaves the stop for this content.
    won = await client.post("/v1/tasks/groomings/claims", json=other)
    assert won.json()["data"]["claimed"] is True
    assert await _listed_stop(client, RECORD["task_gid"]) == STOP["fingerprint"]


async def test_a_record_write_leaves_the_stop_and_groomed_fingerprint(
    client,
) -> None:
    groomed = {**RECORD, "groomed_fingerprint": "b" * 64}
    await client.post("/v1/tasks/groomings", json=groomed)
    await client.post("/v1/tasks/groomings/budget-spent", json=STOP)

    rewritten = {**groomed, "outcome": "questions", "returned_count": 2}
    response = await client.post("/v1/tasks/groomings", json=rewritten)
    data = response.json()["data"]
    assert data["groomed_fingerprint"] == "b" * 64
    assert data["budget_spent_fingerprint"] == STOP["fingerprint"]

    (listed,) = (await client.get("/v1/tasks/groomings")).json()["data"]
    assert listed["groomed_fingerprint"] == "b" * 64
    assert listed["budget_spent_fingerprint"] == STOP["fingerprint"]

    # A stop changes nothing in the record either.
    await client.post(
        "/v1/tasks/groomings/budget-spent", json={**STOP, "fingerprint": "e" * 64}
    )
    (listed,) = (await client.get("/v1/tasks/groomings")).json()["data"]
    assert listed["groomed_fingerprint"] == "b" * 64
    assert listed["fingerprint"] == RECORD["fingerprint"]


@pytest.mark.parametrize(
    "change",
    [{"fingerprint": "not-a-sha"}, {"task_gid": "MINI-151"}, {"extra": True}],
)
async def test_a_malformed_stop_is_rejected(client, change) -> None:
    response = await client.post(
        "/v1/tasks/groomings/budget-spent", json={**STOP, **change}
    )
    assert response.status_code == 422


async def test_a_caller_without_the_scope_may_not_mark_a_stop(
    client, role_client
) -> None:
    async with role_client("agent-reporter") as reporter:
        response = await reporter.post("/v1/tasks/groomings/budget-spent", json=STOP)
        assert response.status_code == 403
    async with role_client("task-groomer") as groomer:
        response = await groomer.post("/v1/tasks/groomings/budget-spent", json=STOP)
        assert response.status_code == 200, response.text
