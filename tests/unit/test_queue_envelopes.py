"""Every queue message the API builds, as the cog that receives it reads it.

Each message is built from the shared envelope model, and each is read back
here with the shared parser a cog's worker uses, so the producer and the
consumer agree on one definition. The bodies are pinned exactly: a cog
deployed before this change must see the same message it always did, and a
FIFO queue deduplicates on the body.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from mini_app_polis.api.contract import (
    DeejayRunMessage,
    EvaluationIntrospectionMessage,
    EvaluationRepositoryMessage,
    QueueMessage,
    TaskUpdateMessage,
    TaskUpdateRequest,
    TranscriptionRunMessage,
    WikiJobMessage,
)
from mini_app_polis.lambda_worker import parse_message

from kaianolevine_api.schemas import WcsWikiJob
from kaianolevine_api.services import (
    deejay_dispatch,
    evaluation_dispatch,
    fleet_registry,
    task_dispatch,
    transcription_dispatch,
    wiki_dispatch,
)

SOURCE = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _body(message: dict[str, Any]) -> str:
    return json.dumps(message)


def _read(message: dict[str, Any], kind: type[QueueMessage[Any]]) -> Any:
    """The message as the receiving cog's worker parses it."""
    return parse_message(_body(message), kind)


def test_a_deejay_run() -> None:
    message = deejay_dispatch.DeejayJob(mode="sync").as_message()
    assert _body(message) == (
        '{"type": "deejay.run", "version": 1, "payload": {"mode": "sync"}}'
    )
    assert _read(message, DeejayRunMessage).payload.mode == "sync"


@pytest.mark.parametrize(
    ("job", "payload"),
    [
        (
            transcription_dispatch.TranscriptionJob(mode="wcs", drive_file_id="f1"),
            '{"mode": "wcs", "drive_file_id": "f1"}',
        ),
        (
            transcription_dispatch.TranscriptionJob(mode="voicenotes-cleanup"),
            '{"mode": "voicenotes-cleanup"}',
        ),
        (
            transcription_dispatch.TranscriptionJob(
                mode="wcs-note", note={"id": "n1", "text": "t"}
            ),
            '{"mode": "wcs-note", "note": {"id": "n1", "text": "t"}}',
        ),
    ],
)
def test_a_transcription_run(
    job: transcription_dispatch.TranscriptionJob, payload: str
) -> None:
    message = job.as_message()
    assert _body(message) == (
        f'{{"type": "transcription.run", "version": 1, "payload": {payload}}}'
    )
    assert _read(message, TranscriptionRunMessage).payload.mode == job.mode


def test_a_wiki_job() -> None:
    job = WcsWikiJob(job="link", source_id=SOURCE)
    message = wiki_dispatch.as_message(job)
    assert _body(message) == (
        '{"type": "wiki.job", "version": 1, '
        f'"payload": {{"job": "link", "source_id": "{SOURCE}"}}}}'
    )
    assert _read(message, WikiJobMessage).payload == job


def test_a_repository_evaluation() -> None:
    message = evaluation_dispatch.EvaluationJob(
        repo="mono", ref="v2", org="other", mode="llm", repo_id="app-a"
    ).as_message()
    assert _body(message) == (
        '{"type": "evaluation.repository", "version": 1, "payload": '
        '{"repo": "mono", "ref": "v2", "org": "other", "mode": "llm", '
        '"repo_id": "app-a"}}'
    )
    assert _read(message, EvaluationRepositoryMessage).payload.repo_id == "app-a"


def test_a_fleet_pass_message() -> None:
    unit = fleet_registry.EvaluationUnit(
        org="mini-app-polis",
        repo="storefront",
        ref="main",
        services=({"id": "shop-web", "repo": "storefront", "path": None},),
        monorepo={"id": "storefront", "apps": None},
    )
    (message,) = evaluation_dispatch.FleetJob(
        mode="deterministic",
        run_id="deterministic-7.0.0-abc",
        standards_version="7.0.0",
    ).messages([unit])
    # Nulls inside the services and the monorepo record are the registry's,
    # and travel as they always did.
    assert _body(message) == (
        '{"type": "evaluation.repository", "version": 1, "payload": '
        '{"repo": "storefront", "ref": "main", "org": "mini-app-polis", '
        '"mode": "deterministic", "run_id": "deterministic-7.0.0-abc", '
        '"services": [{"id": "shop-web", "repo": "storefront", "path": null}], '
        '"monorepo": {"id": "storefront", "apps": null}, '
        '"standards_version": "7.0.0"}}'
    )
    read = _read(message, EvaluationRepositoryMessage)
    assert read.payload.run_id == "deterministic-7.0.0-abc"


def test_an_introspection_run() -> None:
    message = evaluation_dispatch.IntrospectionJob(
        run_id="introspection-7.0.0-abc", pass_run_id="deterministic-7.0.0-abc"
    ).as_message()
    assert _body(message) == (
        '{"type": "evaluation.introspection", "version": 1, "payload": '
        '{"run_id": "introspection-7.0.0-abc", '
        '"pass_run_id": "deterministic-7.0.0-abc"}}'
    )
    read = _read(message, EvaluationIntrospectionMessage)
    assert read.payload.run_id == "introspection-7.0.0-abc"


def test_a_task_update() -> None:
    request = TaskUpdateRequest(external_id="ci.x#1", comment="ok", section="Review")
    message = task_dispatch.update_message(request)
    assert _body(message) == (
        '{"type": "task.update", "version": 1, "payload": '
        '{"external_id": "ci.x#1", "comment": "ok", "section": "Review"}}'
    )
    assert _read(message, TaskUpdateMessage).payload == request


def test_a_task_update_that_moves_nothing_leaves_the_section_out() -> None:
    message = task_dispatch.update_message(
        TaskUpdateRequest(external_id="ci.x#1", comment="ok")
    )
    assert "section" not in message["payload"]
