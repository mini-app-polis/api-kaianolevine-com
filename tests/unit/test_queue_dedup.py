"""The deduplication id every FIFO send carries, and how a collapse is told.

SQS FIFO's own window is a fixed five minutes, so the API sets the window
through the id it sends: a repeat within one bucket of
``DEDUP_WINDOW_SECONDS`` is the same id, and a later re-run is not.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import deejay_dispatch, job_queue

START = 1_000_000.0 - (1_000_000.0 % job_queue.DEDUP_WINDOW_SECONDS)


@pytest.fixture(autouse=True)
def forget_queued() -> Iterator[None]:
    job_queue.forget_queued()
    yield
    job_queue.forget_queued()


def _message(caller: str | None = "watcher-cog") -> dict:
    message = deejay_dispatch.DeejayJob(mode="process-new-files").as_message()
    message["request"] = job_queue.request_identity(caller)
    return message


def test_two_requests_within_a_bucket_share_an_id() -> None:
    """Each request has its own id and time; neither is in the dedup id."""
    first, second = _message(), _message()
    assert first["request"]["id"] != second["request"]["id"]
    assert job_queue.window_deduplication_id(
        first, "deejay", now=START
    ) == job_queue.window_deduplication_id(
        second, "deejay", now=START + job_queue.DEDUP_WINDOW_SECONDS - 1
    )


def test_a_request_in_the_next_bucket_has_its_own_id() -> None:
    message = _message()
    assert job_queue.window_deduplication_id(
        message, "deejay", now=START
    ) != job_queue.window_deduplication_id(
        message, "deejay", now=START + job_queue.DEDUP_WINDOW_SECONDS
    )


def test_the_caller_and_the_group_are_part_of_the_id() -> None:
    a = job_queue.window_deduplication_id(_message("watcher-cog"), "deejay", now=START)
    b = job_queue.window_deduplication_id(_message("operator"), "deejay", now=START)
    c = job_queue.window_deduplication_id(_message("watcher-cog"), "other", now=START)
    assert len({a, b, c}) == 3


def test_an_id_seen_again_is_a_collapse_until_sqs_forgets_it() -> None:
    assert job_queue.was_collapsed("m-1", now=0.0) is False
    assert job_queue.was_collapsed("m-1", now=10.0) is True
    assert job_queue.was_collapsed("m-2", now=10.0) is False
    later = job_queue.SQS_DEDUP_SECONDS + 1.0
    assert job_queue.was_collapsed("m-1", now=later) is False


async def _send_twice(monkeypatch, ids: list[str], clocks: list[float]) -> list:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    settings = get_settings()
    sqs = MagicMock()
    sqs.send_message.side_effect = [{"MessageId": i} for i in ids]
    results = []
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        for clock in clocks:
            with patch.object(job_queue, "_clock", return_value=clock):
                results.append(
                    await deejay_dispatch.dispatch_deejay(
                        deejay_dispatch.DeejayJob(mode="process-new-files"),
                        settings=settings,
                        caller="watcher-cog",
                    )
                )
    return [results, [c.kwargs for c in sqs.send_message.call_args_list]]


async def test_a_repeat_sqs_collapses_says_so_with_the_earlier_id(monkeypatch) -> None:
    """SQS answers a collapsed send with the kept message's id."""
    results, sent = await _send_twice(monkeypatch, ["m-1", "m-1"], [START, START + 5])
    assert results == [
        {"message_id": "m-1"},
        {"message_id": "m-1", "deduplicated": True},
    ]
    assert sent[0]["MessageDeduplicationId"] == sent[1]["MessageDeduplicationId"]
    bodies = [json.loads(s["MessageBody"]) for s in sent]
    assert bodies[0]["request"]["caller"] == "watcher-cog"
    assert bodies[0]["request"]["id"] != bodies[1]["request"]["id"]


async def test_a_rerun_a_minute_later_is_queued(monkeypatch) -> None:
    later = START + job_queue.DEDUP_WINDOW_SECONDS
    results, sent = await _send_twice(monkeypatch, ["m-1", "m-2"], [START, later])
    assert results == [{"message_id": "m-1"}, {"message_id": "m-2"}]
    assert sent[0]["MessageDeduplicationId"] != sent[1]["MessageDeduplicationId"]


async def test_an_explicit_id_is_sent_as_given_and_never_collapsed(monkeypatch) -> None:
    """A job meant to run every time it is asked brings its own id."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        for _ in range(2):
            result = await job_queue.enqueue(
                {"type": "task.sweep", "version": 1},
                "sweep",
                cog="task-manager",
                label="test",
                heading="Test",
                settings=get_settings(),
                deduplication_id="mine",
            )
            assert result == {"message_id": "m-1"}
    assert sqs.send_message.call_args.kwargs["MessageDeduplicationId"] == "mine"


async def test_a_standard_queue_gets_no_deduplication_id(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await job_queue.enqueue(
            {"type": "evaluation.repository", "version": 1, "payload": {}},
            "an evaluation",
            cog="evaluator",
            label="test",
            heading="Test",
            settings=get_settings(),
        )
    sent = sqs.send_message.call_args.kwargs
    assert "MessageDeduplicationId" not in sent
    assert "MessageGroupId" not in sent


@pytest.mark.parametrize(
    ("mode", "queue"),
    [
        ("library-backfill", "deejay-library"),
        ("library-sync", "deejay-library"),
        ("process-new-files", "deejay"),
        ("ingest-live-history", "deejay"),
    ],
)
async def test_a_library_mode_goes_to_the_library_queue(
    monkeypatch, mode: str, queue: str
) -> None:
    """A long library run never queues ahead of intake or live-play ingest."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await deejay_dispatch.dispatch_deejay(
            deejay_dispatch.DeejayJob(mode=mode), settings=get_settings()
        )
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"].endswith(f"/{queue}-jobs.fifo")
    assert sent["MessageGroupId"] == queue
