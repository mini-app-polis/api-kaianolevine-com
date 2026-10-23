"""Tests for the agent runs: the in-flight lock, the spend, and the results."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from identity.store.models import Principal, PrincipalRole
from identity.types import VerifiedSubject
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import auth as auth_mod
from kaianolevine_api import identity_registry
from kaianolevine_api.main import app
from kaianolevine_api.schemas import AgentRun
from kaianolevine_api.services import agent_dispatch, job_queue

pytestmark = pytest.mark.asyncio

ISSUER = "https://clerk.kaianolevine.com"
ACCOUNT_QUEUES = "https://sqs.us-east-1.amazonaws.com/400200465748"
PR = "https://github.com/mini-app-polis/watcher-cog/pull/7"
RUN_URL = "https://github.com/mini-app-polis/watcher-cog/actions/runs/1"

RUN = {
    "task_gid": "1219802464275212",
    "task_id": "MINI-158",
    "repository": "watcher-cog",
    "model": "claude-sonnet-5-5",
}


def _aws(monkeypatch):
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_settings.cache_clear()
    return get_settings()


async def _dispatch(client, **changes) -> dict:
    response = await client.post("/v1/tasks/agent-runs", json={**RUN, **changes})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _queued(message_id: str = "m-1"):
    return patch.object(
        agent_dispatch,
        "dispatch_result",
        AsyncMock(return_value={"message_id": message_id}),
    )


@pytest.fixture
def role_client(
    client, db_session: AsyncSession
) -> Callable[[str], AsyncIterator[httpx.AsyncClient]]:
    """A caller holding only the one role it is given."""

    @asynccontextmanager
    async def make(role: str) -> AsyncIterator[httpx.AsyncClient]:
        subject = f"only-{role}"
        principal = Principal(
            kind="human", issuer=ISSUER, subject=subject, display_name=subject
        )
        db_session.add(principal)
        await db_session.flush()
        db_session.add(
            PrincipalRole(principal_id=principal.id, role_name=role, granted_by="test")
        )
        await db_session.commit()
        original = auth_mod.verify_bearer
        auth_mod.verify_bearer = AsyncMock(
            return_value=VerifiedSubject(issuer=ISSUER, subject=subject, kind="human")
        )
        try:
            async with httpx.ASGITransport(app=app) as transport:
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                    headers={"Authorization": f"Bearer {subject}"},
                ) as c:
                    yield c
        finally:
            auth_mod.verify_bearer = original

    return make


# ── recording runs, and the lock ──────────────────────────────────────────


async def test_a_dispatched_run_is_recorded_and_listed_in_flight(client) -> None:
    run = await _dispatch(client)
    assert run["status"] == "dispatched"
    assert run["cost_usd"] is None
    assert run["finished_at"] is None

    in_flight = (
        await client.get(
            "/v1/tasks/agent-runs",
            params={"status": "dispatched", "repository": "watcher-cog"},
        )
    ).json()["data"]
    assert [r["id"] for r in in_flight] == [run["id"]]
    other = (
        await client.get("/v1/tasks/agent-runs", params={"repository": "infra"})
    ).json()["data"]
    assert other == []


async def test_a_repository_with_a_run_in_flight_gets_no_second(client) -> None:
    await _dispatch(client)
    second = await client.post(
        "/v1/tasks/agent-runs", json={**RUN, "task_gid": "1219802318318907"}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "run_in_flight"

    # Another repository is free.
    await _dispatch(client, repository="wiki-curator-cog")


async def test_a_failed_run_frees_its_repository(client) -> None:
    run = await _dispatch(client)
    failed = await client.post(
        "/v1/tasks/agent-runs/failures",
        json={"run_id": run["id"], "reason": "no result after the job timeout"},
    )
    assert failed.status_code == 200, failed.text
    data = failed.json()["data"]
    assert data["status"] == "failed"
    assert data["cost_usd"] is None
    assert data["finished_at"] is not None

    await _dispatch(client)


async def test_an_ended_run_cannot_end_again(client) -> None:
    run = await _dispatch(client)
    body = {"run_id": run["id"], "reason": "could not start"}
    assert (
        await client.post("/v1/tasks/agent-runs/failures", json=body)
    ).status_code == 200
    again = await client.post("/v1/tasks/agent-runs/failures", json=body)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "run_ended"

    missing = await client.post(
        "/v1/tasks/agent-runs/failures",
        json={"run_id": "0b6f7f5e-3c1a-4c8e-9a51-6f0c2f3d4e5a", "reason": "x"},
    )
    assert missing.status_code == 404


# ── results ───────────────────────────────────────────────────────────────


async def test_a_result_is_recorded_and_queued_for_the_task_manager(
    client, monkeypatch
) -> None:
    _aws(monkeypatch)
    run = await _dispatch(client)
    result = {
        "run_id": run["id"],
        "outcome": "succeeded",
        "cost_usd": 0.84,
        "pr_url": PR,
        "actions_run_url": RUN_URL,
    }
    with _queued("m-9") as dispatched:
        response = await client.post("/v1/tasks/agent-runs/results", json=result)

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "accepted": True,
        "message_id": "m-9",
        "run_id": run["id"],
    }
    queued: AgentRun = dispatched.await_args.args[0]
    assert (queued.status, queued.cost_usd, queued.pr_url) == ("succeeded", 0.84, PR)
    assert queued.task_gid == RUN["task_gid"]

    listed = (await client.get("/v1/tasks/agent-runs")).json()["data"]
    assert listed[0]["status"] == "succeeded"
    assert listed[0]["actions_run_url"] == RUN_URL


async def test_a_second_result_for_a_run_is_refused(client, monkeypatch) -> None:
    _aws(monkeypatch)
    run = await _dispatch(client)
    result = {"run_id": run["id"], "outcome": "capped", "cost_usd": 2.01}
    with _queued() as dispatched:
        first = await client.post("/v1/tasks/agent-runs/results", json=result)
        second = await client.post("/v1/tasks/agent-runs/results", json=result)
    assert first.status_code == 202
    assert second.status_code == 409
    assert dispatched.await_count == 1

    spend = (await client.get("/v1/tasks/agent-runs/spend")).json()["data"]
    assert spend["cost_usd"] == pytest.approx(2.01)


async def test_a_dropped_result_is_not_recorded(client, monkeypatch) -> None:
    _aws(monkeypatch)
    run = await _dispatch(client)
    result = {"run_id": run["id"], "outcome": "failed", "reason": "tests failed"}
    with patch.object(
        agent_dispatch,
        "dispatch_result",
        AsyncMock(side_effect=agent_dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post("/v1/tasks/agent-runs/results", json=result)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"

    # Still in flight, so the post can be made again.
    in_flight = (
        await client.get("/v1/tasks/agent-runs", params={"status": "dispatched"})
    ).json()["data"]
    assert [r["id"] for r in in_flight] == [run["id"]]
    with _queued():
        again = await client.post("/v1/tasks/agent-runs/results", json=result)
    assert again.status_code == 202


@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "succeeded"},  # no pull request
        {"outcome": "failed", "pr_url": PR},
        {"cost_usd": -1},
        {"run_id": "MINI-12"},
        {"extra": True},
    ],
)
async def test_a_malformed_result_is_rejected(client, change) -> None:
    run = await _dispatch(client)
    body = {"run_id": run["id"], "outcome": "failed", **change}
    with _queued() as dispatched:
        response = await client.post("/v1/tasks/agent-runs/results", json=body)
    assert response.status_code == 422
    dispatched.assert_not_awaited()


async def test_a_result_goes_to_the_task_managers_queue(monkeypatch) -> None:
    settings = _aws(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    run = AgentRun.model_validate(
        {
            **RUN,
            "id": "0b6f7f5e-3c1a-4c8e-9a51-6f0c2f3d4e5a",
            "status": "succeeded",
            "cost_usd": 0.5,
            "pr_url": PR,
            "dispatched_at": "2026-10-23T01:00:00Z",
            "finished_at": "2026-10-23T01:20:00Z",
        }
    )
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await agent_dispatch.dispatch_result(run, settings=settings)
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == f"{ACCOUNT_QUEUES}/task-manager-jobs.fifo"
    assert sent["MessageGroupId"] == "task-manager"
    body = json.loads(sent["MessageBody"])
    assert (body["type"], body["version"]) == ("task.agent-result", 1)
    assert AgentRun.model_validate(body["payload"]) == run


# ── spend ─────────────────────────────────────────────────────────────────


async def test_the_months_spend_counts_known_costs_this_month(
    client, monkeypatch, db_session: AsyncSession
) -> None:
    _aws(monkeypatch)
    for repository, cost in (("watcher-cog", 0.4), ("wiki-curator-cog", 1.1)):
        run = await _dispatch(client, repository=repository)
        with _queued():
            await client.post(
                "/v1/tasks/agent-runs/results",
                json={"run_id": run["id"], "outcome": "failed", "cost_usd": cost},
            )
    # In flight: counted as a run, its cost not yet known.
    await _dispatch(client)
    # Last month's run is not this month's spend.
    await db_session.execute(
        text(
            "INSERT INTO task_agent_runs "
            "(task_gid, repository, model, status, cost_usd, dispatched_at) "
            "VALUES ('1', 'infra', 'm', 'failed', 5, now() - interval '40 days')"
        )
    )
    await db_session.commit()

    spend = (await client.get("/v1/tasks/agent-runs/spend")).json()["data"]
    assert spend["runs"] == 3
    assert spend["cost_usd"] == pytest.approx(1.5)
    assert len(spend["month"]) == 7


# ── who may do what ───────────────────────────────────────────────────────


async def test_the_reporter_may_post_results_and_nothing_else(
    client, role_client, monkeypatch
) -> None:
    _aws(monkeypatch)
    run = await _dispatch(client)
    async with role_client("agent-reporter") as reporter:
        assert (
            await reporter.post("/v1/tasks/agent-runs", json=RUN)
        ).status_code == 403
        assert (await reporter.get("/v1/tasks/agent-runs")).status_code == 403
        assert (await reporter.get("/v1/tasks/agent-runs/spend")).status_code == 403
        failure = {"run_id": run["id"], "reason": "x"}
        assert (
            await reporter.post("/v1/tasks/agent-runs/failures", json=failure)
        ).status_code == 403
        with _queued():
            posted = await reporter.post(
                "/v1/tasks/agent-runs/results",
                json={"run_id": run["id"], "outcome": "failed"},
            )
        assert posted.status_code == 202


async def test_the_dispatcher_may_not_post_results(client, role_client) -> None:
    run = await _dispatch(client)
    async with role_client("agent-dispatcher") as dispatcher:
        with _queued() as dispatched:
            response = await dispatcher.post(
                "/v1/tasks/agent-runs/results",
                json={"run_id": run["id"], "outcome": "failed"},
            )
        assert response.status_code == 403
        dispatched.assert_not_awaited()
        assert (await dispatcher.get("/v1/tasks/agent-runs/spend")).status_code == 200


async def test_the_machines_hold_their_agent_roles() -> None:
    manager = identity_registry.declared("task-manager-cog")
    agent = identity_registry.declared("task-agent")
    assert manager is not None and agent is not None
    assert "agent-dispatcher" in manager.roles
    assert "agent-reporter" not in manager.roles
    assert set(agent.roles) == {"agent-reporter", "notifier"}
    assert agent.key_env_var == "TASK_AGENT_API_KEY"
