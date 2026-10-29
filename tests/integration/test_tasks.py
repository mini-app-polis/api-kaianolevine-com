"""Tests for POST /v1/tasks, /v1/tasks/updates, /v1/tasks/sweeps and the task-manager enqueue seam.

The send mechanics live in services.job_queue and are covered through the
evaluator's tests. These pin what is the task path's own: the route, the
scope, the message shape and which queue it goes to.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from identity.store.models import Principal, PrincipalRole
from identity.types import VerifiedSubject
from mini_app_polis.api.contract import TaskCreateMessage, TaskSweepMessage
from mini_app_polis.lambda_worker import parse_message
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import auth as auth_mod
from kaianolevine_api import identity_registry
from kaianolevine_api.main import app
from kaianolevine_api.schemas import TaskCreateRequest, TaskUpdateRequest
from kaianolevine_api.services import job_queue
from kaianolevine_api.services import task_dispatch as dispatch

pytestmark = pytest.mark.asyncio

ISSUER = "https://clerk.kaianolevine.com"
QUEUE = "https://sqs.us-east-1.amazonaws.com/400200465748/task-manager-jobs.fifo"

VOICENOTE = {
    "source": "voicenote",
    "external_id": "voicenote.file-1",
    "draft": {
        "title": "Call the venue",
        "where": "dance",
        "sections": [{"label": "Peers", "text": "Amy"}],
        "links": [{"text": "Listen", "url": "https://drive.google.com/file/d/f/view"}],
        "labels": ["venue"],
    },
}


def _settings(monkeypatch):
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
async def notifier_client(
    client, db_session: AsyncSession
) -> AsyncIterator[httpx.AsyncClient]:
    """A caller holding only ``notifier``."""
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


@pytest.fixture
async def machine_client(client, db_session: AsyncSession):
    """Open a client that authenticates as the named machine, holding ``task-requester``."""
    opened: list[httpx.AsyncClient] = []
    original = auth_mod.verify_bearer

    async def open_as(name: str) -> httpx.AsyncClient:
        principal = Principal(
            kind="machine", issuer=ISSUER, subject=name, display_name=name
        )
        db_session.add(principal)
        await db_session.flush()
        db_session.add(
            PrincipalRole(
                principal_id=principal.id, role_name="task-requester", granted_by="test"
            )
        )
        await db_session.commit()
        auth_mod.verify_bearer = AsyncMock(
            return_value=VerifiedSubject(issuer=ISSUER, subject=name, kind="machine")
        )
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {name}-key"},
        )
        opened.append(c)
        return c

    yield open_as
    for c in opened:
        await c.aclose()
    auth_mod.verify_bearer = original


# ── the route ────────────────────────────────────────────────────────────


async def test_a_request_is_enqueued_and_acknowledged(client, monkeypatch) -> None:
    _settings(monkeypatch)

    with patch.object(
        dispatch, "dispatch_task", AsyncMock(return_value={"message_id": "m-9"})
    ) as dispatched:
        response = await client.post("/v1/tasks", json=VOICENOTE)

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "accepted": True,
        "message_id": "m-9",
        "source": "voicenote",
        "external_id": "voicenote.file-1",
    }
    assert dispatched.await_args.args[0] == TaskCreateRequest.model_validate(VOICENOTE)


async def test_a_dropped_request_is_a_502_not_a_202(client, monkeypatch) -> None:
    _settings(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_task",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post("/v1/tasks", json=VOICENOTE)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


@pytest.mark.parametrize(
    "body",
    [
        {},
        # A source this API does not know.
        {**VOICENOTE, "source": "discord", "external_id": "discord.1"},
        # The key must carry its source.
        {**VOICENOTE, "external_id": "file-1"},
        # Voice-note ids are minted by the task manager.
        {**VOICENOTE, "id": "VN-4"},
        # An issue brings its own id.
        {
            "source": "github-issue",
            "external_id": "github-issue.mini-app-polis/infra#3",
            "draft": {"title": "t"},
        },
        # Fields the contract does not have are refused, not ignored.
        {**VOICENOTE, "draft": {"title": "t", "priority": "high"}},
    ],
)
async def test_a_malformed_request_is_rejected(client, body) -> None:
    with patch.object(dispatch, "dispatch_task", AsyncMock()) as dispatched:
        response = await client.post("/v1/tasks", json=body)

    assert response.status_code == 422
    dispatched.assert_not_awaited()


async def test_a_caller_without_the_scope_is_refused(notifier_client) -> None:
    with patch.object(dispatch, "dispatch_task", AsyncMock()) as dispatched:
        response = await notifier_client.post("/v1/tasks", json=VOICENOTE)

    assert response.status_code == 403
    dispatched.assert_not_awaited()


async def test_a_sweep_is_enqueued_and_acknowledged(client, monkeypatch) -> None:
    _settings(monkeypatch)
    with patch.object(
        dispatch, "dispatch_sweep", AsyncMock(return_value={"message_id": "m-7"})
    ) as dispatched:
        response = await client.post("/v1/tasks/sweeps")

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {"accepted": True, "message_id": "m-7"}
    dispatched.assert_awaited_once()


async def test_a_dropped_sweep_is_a_502_not_a_202(client, monkeypatch) -> None:
    _settings(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_sweep",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post("/v1/tasks/sweeps")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


async def test_a_caller_without_the_sweep_scope_is_refused(notifier_client) -> None:
    with patch.object(dispatch, "dispatch_sweep", AsyncMock()) as dispatched:
        response = await notifier_client.post("/v1/tasks/sweeps")

    assert response.status_code == 403
    dispatched.assert_not_awaited()


async def test_voice_notes_ask_for_their_tasks() -> None:
    """transcription-cog is a source of tasks: each voice note becomes one."""
    cog = identity_registry.declared("transcription-cog")
    assert cog is not None
    assert "task-requester" in cog.roles


async def test_the_task_manager_does_not_ask_for_tasks() -> None:
    """It writes the board; it is not a source of tasks."""
    cog = identity_registry.declared("task-manager-cog")
    assert cog is not None
    assert "task-requester" not in cog.roles
    assert "notifier" in cog.roles


# ── the seam ─────────────────────────────────────────────────────────────


async def test_a_request_goes_to_the_task_managers_queue(monkeypatch) -> None:
    """The envelope both sides agree on, on task-manager's queue."""
    settings = _settings(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}

    with patch.object(job_queue.boto3, "client", return_value=sqs):
        result = await dispatch.dispatch_task(
            TaskCreateRequest.model_validate(VOICENOTE), settings=settings
        )

    assert result == {"message_id": "m-1"}
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == QUEUE
    # FIFO, one group: the board is written one job at a time.
    assert sent["MessageGroupId"] == "task-manager"
    body = json.loads(sent["MessageBody"])
    assert body["type"] == "task.create"
    assert body["version"] == 1
    # What task-manager-cog's worker reads it as.
    assert parse_message(sent["MessageBody"], TaskCreateMessage).payload == (
        TaskCreateRequest.model_validate(VOICENOTE)
    )
    assert TaskCreateRequest.model_validate(body["payload"]) == (
        TaskCreateRequest.model_validate(VOICENOTE)
    )
    # Unset optional fields are left out rather than sent as null.
    assert "id" not in body["payload"]


async def test_a_sweep_goes_to_the_task_managers_queue(monkeypatch) -> None:
    """The schedule's own message, in the queue's one group."""
    settings = _settings(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}

    with patch.object(job_queue.boto3, "client", return_value=sqs):
        result = await dispatch.dispatch_sweep(settings=settings)

    assert result == {"message_id": "m-1"}
    sent = sqs.send_message.call_args.kwargs
    assert sent["QueueUrl"] == QUEUE
    # In line with every job that writes the board, so never beside one.
    assert sent["MessageGroupId"] == "task-manager"
    # Byte for byte what infra's schedule sends.
    assert sent["MessageBody"] == '{"type": "task.sweep", "version": 1}'
    assert json.loads(sent["MessageBody"]) == {"type": "task.sweep", "version": 1}
    # What task-manager-cog's worker reads it as.
    assert isinstance(
        parse_message(sent["MessageBody"], TaskSweepMessage), TaskSweepMessage
    )


async def test_each_sweep_asked_for_is_its_own_job(monkeypatch) -> None:
    """The body never changes, so the queue would merge two within five
    minutes; a deduplication id per request keeps them two sweeps."""
    settings = _settings(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}

    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await dispatch.dispatch_sweep(settings=settings)
        await dispatch.dispatch_sweep(settings=settings)

    first, second = (c.kwargs for c in sqs.send_message.call_args_list)
    assert first["MessageBody"] == second["MessageBody"]
    assert first["MessageDeduplicationId"]
    assert first["MessageDeduplicationId"] != second["MessageDeduplicationId"]


async def test_a_task_request_leaves_deduplication_to_the_queue(monkeypatch) -> None:
    """Only the sweep brings its own id; other jobs dedupe on the body."""
    settings = _settings(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}

    with patch.object(job_queue.boto3, "client", return_value=sqs):
        await dispatch.dispatch_task(
            TaskCreateRequest.model_validate(VOICENOTE), settings=settings
        )

    assert "MessageDeduplicationId" not in sqs.send_message.call_args.kwargs


# ── updates ──────────────────────────────────────────────────────────────

ROTATION_UPDATE = {
    "external_id": "secret-rotation.prd.OPENAI_API_KEY.2026-10",
    "comment": "Rotated on 2026-10-26; check passed.",
    "section": "Review",
}


async def test_a_machine_updates_its_own_task(machine_client, monkeypatch) -> None:
    _settings(monkeypatch)
    rotator = await machine_client("rotator-cog")

    with patch.object(
        dispatch, "dispatch_task_update", AsyncMock(return_value={"message_id": "m-3"})
    ) as dispatched:
        response = await rotator.post("/v1/tasks/updates", json=ROTATION_UPDATE)

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "accepted": True,
        "message_id": "m-3",
        "external_id": ROTATION_UPDATE["external_id"],
    }
    assert dispatched.await_args.args[0] == TaskUpdateRequest.model_validate(
        ROTATION_UPDATE
    )


async def test_an_update_may_only_comment(machine_client, monkeypatch) -> None:
    _settings(monkeypatch)
    rotator = await machine_client("rotator-cog")
    body = {k: v for k, v in ROTATION_UPDATE.items() if k != "section"}

    with patch.object(
        dispatch, "dispatch_task_update", AsyncMock(return_value={"message_id": "m-4"})
    ):
        response = await rotator.post("/v1/tasks/updates", json=body)

    assert response.status_code == 202, response.text


@pytest.mark.parametrize(
    ("machine", "external_id"),
    [
        # Another source's task.
        ("rotator-cog", "voicenote.file-1"),
        # A prefix that only starts like an owned source.
        ("rotator-cog", "secret-rotationX.prd.A.1"),
        # A machine that owns no task source.
        ("deejay-cog", "secret-rotation.prd.A.1"),
    ],
)
async def test_a_machine_may_not_update_another_sources_task(
    machine_client, monkeypatch, machine, external_id
) -> None:
    _settings(monkeypatch)
    caller = await machine_client(machine)

    with patch.object(dispatch, "dispatch_task_update", AsyncMock()) as dispatched:
        response = await caller.post(
            "/v1/tasks/updates", json={**ROTATION_UPDATE, "external_id": external_id}
        )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    dispatched.assert_not_awaited()


async def test_an_update_may_only_move_a_task_to_review(
    machine_client, monkeypatch
) -> None:
    _settings(monkeypatch)
    rotator = await machine_client("rotator-cog")

    with patch.object(dispatch, "dispatch_task_update", AsyncMock()) as dispatched:
        response = await rotator.post(
            "/v1/tasks/updates", json={**ROTATION_UPDATE, "section": "Ready"}
        )

    assert response.status_code == 422, response.text
    dispatched.assert_not_awaited()


async def test_a_dropped_update_is_a_502_not_a_202(client, monkeypatch) -> None:
    _settings(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_task_update",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post("/v1/tasks/updates", json=ROTATION_UPDATE)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


async def test_a_caller_without_the_update_scope_is_refused(notifier_client) -> None:
    response = await notifier_client.post("/v1/tasks/updates", json=ROTATION_UPDATE)
    assert response.status_code == 403


async def test_a_machine_may_only_ask_for_tasks_under_its_own_source(
    machine_client, monkeypatch
) -> None:
    _settings(monkeypatch)
    rotator = await machine_client("rotator-cog")

    with patch.object(dispatch, "dispatch_task", AsyncMock()) as dispatched:
        response = await rotator.post("/v1/tasks", json=VOICENOTE)

    assert response.status_code == 403, response.text
    dispatched.assert_not_awaited()


async def test_each_task_requester_owns_its_source() -> None:
    """A machine that asks for tasks names the source it asks under."""
    assert identity_registry.task_sources_for("transcription-cog") == {"voicenote"}
    assert identity_registry.task_sources_for("rotator-cog") == {"secret-rotation"}
    for machine in identity_registry.MACHINES:
        if "task-requester" in machine.roles:
            assert machine.task_sources, machine.name


# ── a Claude session files manual tasks ──────────────────────────────────

MANUAL = {
    "source": "manual",
    "external_id": "manual.2026-10-29-claude-session-1",
    "draft": {"title": "A task filed from a Claude session"},
}


@pytest.fixture
async def declared_machine(client, db_session: AsyncSession, monkeypatch):
    """Authenticate as a machine with exactly the roles identity_registry declares."""
    from identity.apikey import API_KEY_ISSUER

    await identity_registry.reconcile(db_session)
    await db_session.commit()

    def as_machine(name: str) -> httpx.AsyncClient:
        monkeypatch.setattr(
            auth_mod,
            "verify_bearer",
            AsyncMock(
                return_value=VerifiedSubject(
                    issuer=API_KEY_ISSUER, subject=name, kind="machine"
                )
            ),
        )
        return client

    return as_machine


async def test_the_claude_session_owns_manual_and_can_only_file() -> None:
    session = identity_registry.declared("claude-session")
    assert session is not None
    assert session.roles == ("task-filer",)
    assert session.task_sources == ("manual",)
    assert session.key_env_var == "CLAUDE_SESSION_API_KEY"
    assert not session.development_only


async def test_the_claude_session_files_a_manual_task(
    declared_machine, monkeypatch
) -> None:
    _settings(monkeypatch)
    session = declared_machine("claude-session")
    with patch.object(
        dispatch, "dispatch_task", AsyncMock(return_value={"message_id": "m-1"})
    ) as dispatched:
        response = await session.post("/v1/tasks", json=MANUAL)
    assert response.status_code == 202, response.text
    assert response.json()["data"]["source"] == "manual"
    dispatched.assert_awaited_once()


async def test_the_claude_session_may_file_under_no_other_source(
    declared_machine, monkeypatch
) -> None:
    _settings(monkeypatch)
    session = declared_machine("claude-session")
    with patch.object(dispatch, "dispatch_task", AsyncMock()) as dispatched:
        response = await session.post("/v1/tasks", json=VOICENOTE)
    assert response.status_code == 403
    assert "own sources (manual)" in response.json()["error"]["message"]
    dispatched.assert_not_awaited()


async def test_the_claude_session_can_do_nothing_but_file(declared_machine) -> None:
    session = declared_machine("claude-session")
    update = {"external_id": MANUAL["external_id"], "comment": "done"}
    assert (await session.post("/v1/tasks/updates", json=update)).status_code == 403
    assert (await session.post("/v1/tasks/sweeps")).status_code == 403
    assert (await session.get("/v1/tasks/groomings")).status_code == 403
    assert (
        await session.post("/v1/notify", json={"content": "hello"})
    ).status_code == 403


async def test_no_other_machine_may_file_a_manual_task(
    declared_machine, monkeypatch
) -> None:
    _settings(monkeypatch)
    cog = declared_machine("transcription-cog")
    with patch.object(dispatch, "dispatch_task", AsyncMock()) as dispatched:
        response = await cog.post("/v1/tasks", json=MANUAL)
    assert response.status_code == 403
    dispatched.assert_not_awaited()
    for machine in identity_registry.MACHINES:
        if machine.name != "claude-session":
            assert "manual" not in machine.task_sources, machine.name
