"""Tests for POST /v1/evaluations/runs, /sweeps, and the enqueue seam."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from kaianolevine_api.services import evaluation_dispatch as dispatch

pytestmark = pytest.mark.asyncio

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/400200465748/evaluator-jobs"


def _configured(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATION_QUEUE_URL", QUEUE_URL)
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def _settings(monkeypatch):
    from kaianolevine_api.config import get_settings

    _configured(monkeypatch)
    get_settings.cache_clear()
    return get_settings()


def _sqs(message_id: str = "m-1", side_effect=None) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.send_message.side_effect = side_effect
    else:
        client.send_message.return_value = {"MessageId": message_id}
    return client


# ── the routes ───────────────────────────────────────────────────────────


async def test_run_is_enqueued_and_acknowledged(client, monkeypatch) -> None:
    """202, naming the message rather than a run id it cannot know."""
    _configured(monkeypatch)

    with patch.object(
        dispatch, "dispatch_evaluation", AsyncMock(return_value={"message_id": "m-9"})
    ) as dispatched:
        response = await client.post(
            "/v1/evaluations/runs",
            json={"repo": "watcher-cog", "ref": "v1.2.3"},
        )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["message_id"] == "m-9"
    # The evaluator mints the run id from the catalog version it actually
    # grades against, which is resolved when the job runs. Stamping one
    # here would be a number the run might not have used.
    assert data["run_id"] == ""
    assert data["repo"] == "watcher-cog"
    assert data["ref"] == "v1.2.3"
    assert data["mode"] == "deterministic"

    job = dispatched.await_args.args[0]
    assert (job.repo, job.ref, job.org) == ("watcher-cog", "v1.2.3", "mini-app-polis")


async def test_a_caller_supplied_run_id_is_echoed_back(client, monkeypatch) -> None:
    """A fleet pass keeps one run id across every repository."""
    _configured(monkeypatch)

    with patch.object(
        dispatch, "dispatch_evaluation", AsyncMock(return_value={"message_id": "m-9"})
    ):
        response = await client.post(
            "/v1/evaluations/runs",
            json={"repo": "watcher-cog", "run_id": "deterministic-7.0.0-abc"},
        )

    assert response.json()["data"]["run_id"] == "deterministic-7.0.0-abc"


async def test_a_dropped_job_is_a_502_not_a_202(client, monkeypatch) -> None:
    """The caller will not read this, which is exactly why it must not be 202.

    A job that never reaches the queue produces no findings and no
    failures, and leaves the repository's record looking healthy where it
    stopped. That is the September shape.
    """
    _configured(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_evaluation",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post(
            "/v1/evaluations/runs", json={"repo": "watcher-cog"}
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


async def test_an_unknown_mode_is_rejected(client) -> None:
    response = await client.post(
        "/v1/evaluations/runs", json={"repo": "watcher-cog", "mode": "guess"}
    )
    assert response.status_code == 422


# ── the seam itself ──────────────────────────────────────────────────────


async def test_enqueue_sends_the_repository_message_shape(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    sqs = _sqs("m-1")

    job = dispatch.EvaluationJob(
        repo="mono", ref="v2", org="other", mode="llm", repo_id="app-a"
    )
    with patch.object(dispatch.boto3, "client", return_value=sqs) as factory:
        result = await dispatch.dispatch_evaluation(job, settings=settings)

    assert result == {"message_id": "m-1"}
    assert factory.call_args.kwargs["region_name"] == "us-east-1"

    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == QUEUE_URL
    assert json.loads(sent["MessageBody"]) == {
        "type": dispatch.TYPE_REPOSITORY,
        "version": dispatch.MESSAGE_VERSION,
        "payload": {
            "repo": "mono",
            "ref": "v2",
            "org": "other",
            "mode": "llm",
            "repo_id": "app-a",
        },
    }
    # Readable without parsing the body — one queue serves the whole fleet,
    # so a consumer has to recognise a shape it does not handle.
    assert sent["MessageAttributes"]["type"]["StringValue"] == (
        dispatch.TYPE_REPOSITORY
    )


async def test_enqueue_sends_the_sweep_message_shape(monkeypatch) -> None:
    """A different type on the same queue, and no repository named."""
    settings = _settings(monkeypatch)
    sqs = _sqs("m-2")

    with patch.object(dispatch.boto3, "client", return_value=sqs):
        result = await dispatch.dispatch_sweep(
            dispatch.SweepJob(mode="llm"), settings=settings
        )

    assert result == {"message_id": "m-2"}
    body = json.loads(sqs.send_message.call_args.kwargs["MessageBody"])
    assert body["type"] == dispatch.TYPE_SWEEP
    assert body["payload"] == {"mode": "llm"}


async def test_missing_configuration_is_named(monkeypatch) -> None:
    """An unconfigured dispatcher and an unreachable queue differ."""
    from kaianolevine_api.config import get_settings

    monkeypatch.delenv("EVALUATION_QUEUE_URL", raising=False)
    get_settings.cache_clear()

    with patch.object(dispatch, "_report", AsyncMock()) as reported:
        with pytest.raises(dispatch.DispatchError, match="not configured"):
            await dispatch.dispatch_evaluation(
                dispatch.EvaluationJob(
                    repo="x", ref="main", org="o", mode="deterministic"
                ),
                settings=get_settings(),
            )

    assert "EVALUATION_QUEUE_URL" in reported.await_args.args[0]


async def test_a_queue_refusal_reports_to_the_errors_channel(monkeypatch) -> None:
    """Nobody is waiting on the CI side, so the channel is the witness."""
    settings = _settings(monkeypatch)
    refusal = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "SendMessage"
    )
    sqs = _sqs(side_effect=refusal)

    with (
        patch.object(dispatch.boto3, "client", return_value=sqs),
        patch.object(dispatch, "_report", AsyncMock()) as reported,
    ):
        with pytest.raises(dispatch.DispatchError):
            await dispatch.dispatch_evaluation(
                dispatch.EvaluationJob(
                    repo="x", ref="main", org="o", mode="deterministic"
                ),
                settings=settings,
            )

    assert "could not enqueue" in reported.await_args.args[0]


async def test_absent_credentials_are_a_dropped_job_like_any_other(
    monkeypatch,
) -> None:
    """The producer's key is the one long-lived credential in this system.

    A rotation that misses this service must not look like a quiet success:
    NoCredentialsError is a BotoCoreError, not a ClientError, and both have
    to land on the same path or an expired key becomes a silent outage.
    """
    settings = _settings(monkeypatch)
    sqs = _sqs(side_effect=NoCredentialsError())

    with (
        patch.object(dispatch.boto3, "client", return_value=sqs),
        patch.object(dispatch, "_report", AsyncMock()) as reported,
    ):
        with pytest.raises(dispatch.DispatchError):
            await dispatch.dispatch_sweep(
                dispatch.SweepJob(mode="deterministic"), settings=settings
            )

    assert "could not enqueue" in reported.await_args.args[0]


async def test_an_acknowledgement_without_a_message_id_is_a_failure(
    monkeypatch,
) -> None:
    """Should be impossible. If it happens the job is in an unknown state."""
    settings = _settings(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {}

    with (
        patch.object(dispatch.boto3, "client", return_value=sqs),
        patch.object(dispatch, "_report", AsyncMock()) as reported,
    ):
        with pytest.raises(dispatch.DispatchError, match="message id"):
            await dispatch.dispatch_evaluation(
                dispatch.EvaluationJob(
                    repo="x", ref="main", org="o", mode="deterministic"
                ),
                settings=settings,
            )

    assert "no MessageId" in reported.await_args.args[0]


# ── the sweep routes ─────────────────────────────────────────────────────


async def test_sweep_is_enqueued_and_acknowledged(client, monkeypatch) -> None:
    _configured(monkeypatch)

    with patch.object(
        dispatch, "dispatch_sweep", AsyncMock(return_value={"message_id": "m-3"})
    ) as dispatched:
        response = await client.post("/v1/evaluations/sweeps", json={})

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["message_id"] == "m-3"
    assert data["mode"] == "deterministic"

    job = dispatched.await_args.args[0]
    assert job.mode == "deterministic"
    assert job.run_id is None


async def test_a_dropped_sweep_is_a_502(client, monkeypatch) -> None:
    """Quieter than a dropped run and worse — every repo keeps a stale grade."""
    _configured(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_sweep",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post("/v1/evaluations/sweeps", json={})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


async def test_an_unknown_sweep_mode_is_rejected(client) -> None:
    response = await client.post("/v1/evaluations/sweeps", json={"mode": "guess"})
    assert response.status_code == 422


async def test_the_producer_uses_its_own_named_credentials(monkeypatch) -> None:
    """Not boto3's AWS_ACCESS_KEY_ID.

    The consumer holds a receive-only key and this holds a send-only one,
    and the fleet keeps its secrets in one store. Under the conventional
    names the two collide and one service ends up holding a credential
    that cannot do its job.
    """
    monkeypatch.setenv("EVALUATION_QUEUE_PRODUCER_KEY_ID", "AKIAPRODUCER")
    monkeypatch.setenv("EVALUATION_QUEUE_PRODUCER_SECRET", "producer-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIACONSUMER")  # wrong for this side
    settings = _settings(monkeypatch)

    with patch.object(dispatch.boto3, "client", return_value=_sqs()) as factory:
        await dispatch.dispatch_sweep(
            dispatch.SweepJob(mode="deterministic"), settings=settings
        )

    assert factory.call_args.kwargs["aws_access_key_id"] == "AKIAPRODUCER"
    assert factory.call_args.kwargs["aws_secret_access_key"] == "producer-secret"


async def test_absent_credentials_fall_through_to_the_default_chain(
    monkeypatch,
) -> None:
    """For any runtime that supplies a role instead of a key."""
    monkeypatch.delenv("EVALUATION_QUEUE_PRODUCER_KEY_ID", raising=False)
    monkeypatch.delenv("EVALUATION_QUEUE_PRODUCER_SECRET", raising=False)
    settings = _settings(monkeypatch)

    with patch.object(dispatch.boto3, "client", return_value=_sqs()) as factory:
        await dispatch.dispatch_sweep(
            dispatch.SweepJob(mode="deterministic"), settings=settings
        )

    assert "aws_access_key_id" not in factory.call_args.kwargs
