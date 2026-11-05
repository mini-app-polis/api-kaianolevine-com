"""The grooming names of the refining routes, served until every cog moves.

A cog still on its grooming release calls /v1/tasks/groomings under the
tasks.groomings scopes and reads the record under its grooming field names;
its jobs go to the groomer still running, on task-groomer-jobs. These tests
go with those routes.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from mini_app_polis.api.contract import TaskGroomMessage, TaskGroomProposalMessage
from mini_app_polis.lambda_worker import parse_message

from kaianolevine_api import identity_registry
from kaianolevine_api.schemas import RefiningJobRequest, RefiningProposal
from kaianolevine_api.services import job_queue, refining_dispatch

pytestmark = pytest.mark.asyncio

ACCOUNT_QUEUES = "https://sqs.us-east-1.amazonaws.com/400200465748"

RECORD = {
    "task_gid": "1219755103049631",
    "task_id": "TASK-151",
    "fingerprint": "a" * 64,
    "outcome": "questions",
    "problems": [],
}

JOB = {
    "task": {
        "task_gid": "1219755103049631",
        "name": "TASK-38 · idea",
        "fingerprint": "c" * 64,
    },
}

PROPOSAL = {
    "task_gid": "1219755103049631",
    "fingerprint": "c" * 64,
    "title": "Dashboard for TV shows and movies",
    "model": "claude-sonnet-5-5",
    "cost_usd": 0.11,
}


def _aws(monkeypatch):
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_settings.cache_clear()
    return get_settings()


async def test_a_record_written_under_grooming_names_is_one_record(client) -> None:
    sent = {
        **RECORD,
        "groomed_fingerprint": RECORD["fingerprint"],
        "groom_sent_at": "2026-10-28T09:15:00Z",
        "groom_attempts": 2,
    }
    written = await client.post("/v1/tasks/groomings", json=sent)
    assert written.status_code == 200, written.text
    data = written.json()["data"]
    assert data["groomed_fingerprint"] == RECORD["fingerprint"]
    assert data["groom_attempts"] == 2
    assert "refined_fingerprint" not in data

    refining = (await client.get("/v1/tasks/refinings")).json()["data"]
    assert refining[0]["refined_fingerprint"] == RECORD["fingerprint"]
    assert refining[0]["refine_attempts"] == 2
    grooming = (await client.get("/v1/tasks/groomings")).json()["data"]
    assert grooming[0]["groom_sent_at"].startswith("2026-10-28T09:15:00")


async def test_a_grooming_record_refuses_refining_names(client) -> None:
    response = await client.post(
        "/v1/tasks/groomings", json={**RECORD, "refine_attempts": 1}
    )
    assert response.status_code == 422


async def test_the_month_spend_is_one_figure_under_both_names(client) -> None:
    run = {
        "task_gid": RECORD["task_gid"],
        "model": "claude-sonnet-5-5",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.25,
    }
    assert (await client.post("/v1/tasks/groomings/runs", json=run)).status_code == 201
    assert (await client.post("/v1/tasks/refinings/runs", json=run)).status_code == 201
    old = (await client.get("/v1/tasks/groomings/spend")).json()["data"]
    new = (await client.get("/v1/tasks/refinings/spend")).json()["data"]
    assert old == new
    assert new["runs"] == 2


async def test_a_grooming_job_goes_to_the_groomers_queue(monkeypatch) -> None:
    settings = _aws(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await refining_dispatch.dispatch_grooming_job(
            RefiningJobRequest.model_validate(JOB), settings=settings
        )
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == f"{ACCOUNT_QUEUES}/task-groomer-jobs"
    assert json.loads(sent["MessageBody"])["type"] == "task.groom"
    assert isinstance(
        parse_message(sent["MessageBody"], TaskGroomMessage), TaskGroomMessage
    )


async def test_a_grooming_proposal_reaches_the_task_manager_as_one(
    monkeypatch,
) -> None:
    settings = _aws(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-2"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await refining_dispatch.dispatch_grooming_proposal(
            RefiningProposal.model_validate(PROPOSAL), settings=settings
        )
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == f"{ACCOUNT_QUEUES}/task-manager-jobs.fifo"
    assert json.loads(sent["MessageBody"])["type"] == "task.groom-proposal"
    assert isinstance(
        parse_message(sent["MessageBody"], TaskGroomProposalMessage),
        TaskGroomProposalMessage,
    )


async def test_the_task_manager_holds_both_names_until_it_moves() -> None:
    cog = identity_registry.declared("task-manager-cog")
    assert cog is not None
    assert {"task-refiner", "task-groomer"} <= set(cog.roles)


async def test_the_running_groomer_keeps_its_grooming_role() -> None:
    cog = identity_registry.declared("task-groomer-cog")
    assert cog is not None
    assert "task-groomer" in cog.roles
