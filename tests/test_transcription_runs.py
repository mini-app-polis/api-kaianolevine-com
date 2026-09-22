"""Tests for POST /v1/transcription/runs and the transcription enqueue seam.

The send mechanics — credentials, the MessageId check, reporting a drop —
live in services.job_queue and are covered through the evaluator's tests
in test_evaluation_runs.py. These pin what is transcription's own: the
route, the scope, the message shape and which queue it goes to.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from botocore.exceptions import ClientError
from identity.store.models import Principal, PrincipalRole
from identity.types import VerifiedSubject
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import auth as auth_mod
from kaianolevine_api import identity_registry
from kaianolevine_api.main import app
from kaianolevine_api.services import job_queue
from kaianolevine_api.services import transcription_dispatch as dispatch

pytestmark = pytest.mark.asyncio

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/400200465748/transcription-jobs"
ISSUER = "https://clerk.kaianolevine.com"


def _settings(monkeypatch):
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_settings.cache_clear()
    return get_settings()


def _sqs(message_id: str = "m-1", side_effect=None) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.send_message.side_effect = side_effect
    else:
        client.send_message.return_value = {"MessageId": message_id}
    return client


@pytest.fixture
async def notifier_client(
    client, db_session: AsyncSession
) -> AsyncIterator[httpx.AsyncClient]:
    """A caller holding only ``notifier`` — watcher-cog before this change."""
    principal = Principal(
        kind="human", issuer=ISSUER, subject="notifier-only", display_name="n"
    )
    db_session.add(principal)
    await db_session.flush()
    db_session.add(
        PrincipalRole(
            principal_id=principal.id, role_name="notifier", granted_by="test"
        )
    )
    await db_session.commit()

    original = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(
        return_value=VerifiedSubject(
            issuer=ISSUER, subject="notifier-only", kind="human"
        )
    )
    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer notifier-token"},
        ) as c:
            yield c
    auth_mod.verify_bearer = original


# ── the route ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["wcs-transcripts", "voicenotes"])
async def test_a_file_run_is_enqueued_and_acknowledged(
    client, monkeypatch, mode
) -> None:
    """202, naming the message it became. Both of watcher's modes."""
    _settings(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_transcription",
        AsyncMock(return_value={"message_id": "m-9"}),
    ) as dispatched:
        response = await client.post(
            "/v1/transcription/runs", json={"mode": mode, "drive_file_id": "f-1"}
        )

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "accepted": True,
        "message_id": "m-9",
        "mode": mode,
        "drive_file_id": "f-1",
    }
    assert dispatched.await_args.args[0] == dispatch.TranscriptionJob(
        mode=mode, drive_file_id="f-1"
    )


async def test_the_retention_sweep_names_no_file(client, monkeypatch) -> None:
    _settings(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_transcription",
        AsyncMock(return_value={"message_id": "m-10"}),
    ) as dispatched:
        response = await client.post(
            "/v1/transcription/runs", json={"mode": "voicenotes-cleanup"}
        )

    assert response.status_code == 202, response.text
    assert response.json()["data"]["drive_file_id"] is None
    assert dispatched.await_args.args[0] == dispatch.TranscriptionJob(
        mode="voicenotes-cleanup"
    )


async def test_a_dropped_job_is_a_502_not_a_202(client, monkeypatch) -> None:
    """Watcher will not read this, which is exactly why it must not be 202."""
    _settings(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_transcription",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post(
            "/v1/transcription/runs",
            json={"mode": "wcs-transcripts", "drive_file_id": "f-1"},
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "guess", "drive_file_id": "f-1"},
        {},
        # The old voicenotes-router's own mode names are not this API's.
        {"mode": "ingest", "drive_file_id": "f-1"},
        # A file mode with no file: the folder sweep this replaced.
        {"mode": "wcs-transcripts"},
        {"mode": "voicenotes"},
        {"mode": "wcs-transcripts", "drive_file_id": ""},
        # Extra fields are refused: a watcher sending something this route
        # ignores is a watcher that believes it said something it did not.
        {"mode": "wcs-transcripts", "drive_file_id": "f-1", "folder_id": "abc"},
        # The retention sweep works on the archive, never on one file.
        {"mode": "voicenotes-cleanup", "drive_file_id": "f-1"},
    ],
)
async def test_a_malformed_request_is_rejected(client, body) -> None:
    with patch.object(dispatch, "dispatch_transcription", AsyncMock()) as dispatched:
        response = await client.post("/v1/transcription/runs", json=body)

    assert response.status_code == 422
    dispatched.assert_not_awaited()


async def test_a_caller_without_the_scope_is_refused(notifier_client) -> None:
    """notifier alone is not enough to start a run."""
    with patch.object(dispatch, "dispatch_transcription", AsyncMock()) as dispatched:
        response = await notifier_client.post(
            "/v1/transcription/runs",
            json={"mode": "wcs-transcripts", "drive_file_id": "f-1"},
        )

    assert response.status_code == 403
    dispatched.assert_not_awaited()


async def test_watcher_is_declared_with_the_trigger_role() -> None:
    """The grant is a code change; this is the line that makes it."""
    watcher = identity_registry.declared("watcher-cog")
    assert watcher is not None
    assert "transcription-trigger" in watcher.roles


async def test_transcription_cog_cannot_trigger_itself() -> None:
    """The cog does the work; only the watcher asks for it."""
    cog = identity_registry.declared("transcription-cog")
    assert cog is not None
    assert "transcription-trigger" not in cog.roles


# ── the seam ─────────────────────────────────────────────────────────────


async def test_a_file_message_goes_to_transcriptions_queue(monkeypatch) -> None:
    """The envelope both sides agree on, on transcription's queue."""
    settings = _settings(monkeypatch)
    sqs = _sqs("m-1")

    with patch.object(job_queue.boto3, "client", return_value=sqs):
        result = await dispatch.dispatch_transcription(
            dispatch.TranscriptionJob(mode="voicenotes", drive_file_id="f-7"),
            settings=settings,
        )

    assert result == {"message_id": "m-1"}
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == QUEUE_URL
    assert json.loads(sent["MessageBody"]) == {
        "type": "transcription.run",
        "version": 1,
        "payload": {"mode": "voicenotes", "drive_file_id": "f-7"},
    }
    assert sent["MessageAttributes"]["type"]["StringValue"] == "transcription.run"


async def test_the_cleanup_message_carries_no_file(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    sqs = _sqs("m-2")

    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await dispatch.dispatch_transcription(
            dispatch.TranscriptionJob(mode="voicenotes-cleanup"), settings=settings
        )

    assert json.loads(sqs.send_message.call_args.kwargs["MessageBody"]) == {
        "type": "transcription.run",
        "version": 1,
        "payload": {"mode": "voicenotes-cleanup"},
    }


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("production", "transcription-jobs"),
        # An alias must resolve like the canonical name. Compared as a raw
        # string, "prod" would send production's work to the dev queue.
        ("prod", "transcription-jobs"),
        ("development", "transcription-dev-jobs"),
        # A local API has no queue of its own; the one thing it must never
        # reach is production's.
        ("local", "transcription-dev-jobs"),
    ],
)
async def test_the_queue_is_derived_from_cog_and_environment(
    monkeypatch, environment, expected
) -> None:
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", environment)
    get_settings.cache_clear()
    sqs = _sqs()

    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await dispatch.dispatch_transcription(
            dispatch.TranscriptionJob(mode="wcs-transcripts"), settings=get_settings()
        )

    assert sqs.send_message.call_args.kwargs["QueueUrl"] == (
        f"https://sqs.us-east-1.amazonaws.com/400200465748/{expected}"
    )


async def test_a_missing_dev_queue_is_reported_by_name(monkeypatch) -> None:
    """Until a dev stack exists, a dev enqueue fails — and says which queue."""
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "development")
    get_settings.cache_clear()
    missing = ClientError(
        {"Error": {"Code": "AWS.SimpleQueueService.NonExistentQueue"}}, "SendMessage"
    )

    with (
        patch.object(job_queue.boto3, "client", return_value=_sqs(side_effect=missing)),
        patch.object(dispatch, "_report", AsyncMock()) as reported,
    ):
        with pytest.raises(dispatch.DispatchError):
            await dispatch.dispatch_transcription(
                dispatch.TranscriptionJob(mode="wcs-transcripts"),
                settings=get_settings(),
            )

    assert "onto transcription-dev-jobs" in reported.await_args.args[0]


async def test_a_queue_refusal_reports_as_transcription(monkeypatch) -> None:
    """The drop reaches transcription's reporter, naming the file it lost."""
    settings = _settings(monkeypatch)
    refusal = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "SendMessage"
    )

    with (
        patch.object(job_queue.boto3, "client", return_value=_sqs(side_effect=refusal)),
        patch.object(job_queue.discord, "send_message", AsyncMock()) as sent,
    ):
        with pytest.raises(dispatch.DispatchError):
            await dispatch.dispatch_transcription(
                dispatch.TranscriptionJob(mode="voicenotes", drive_file_id="f-3"),
                settings=settings,
            )

    content = sent.await_args.kwargs["payload"]["content"]
    assert content.startswith("Transcription run not dispatched — could not enqueue")
    assert "file f-3" in content
    assert sent.await_args.kwargs["context"] == "transcription-dispatch"
