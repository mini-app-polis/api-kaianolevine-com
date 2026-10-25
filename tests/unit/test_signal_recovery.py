"""What a recovered streak's task is told, and that telling it never fails.

The engine's database side is covered by tests/integration/test_signals.py;
these are the parts that need no database: the comment, which must be the
same text for a redelivered recovery, the detectors' recovered signals, and
a request that does not land.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import (
    ci_signals,
    job_queue,
    run_signals,
    signals,
    task_dispatch,
)

LINK = "https://github.com/mini-app-polis/infra/actions/runs/2"


def _recovered(**overrides) -> signals.Signal:
    base = {
        "kind": "ci",
        "key": "mini-app-polis/infra/CI@dev",
        "state": "recovered",
        "title": "infra: CI passing on dev",
        "link": LINK,
    }
    base.update(overrides)
    return signals.Signal(**base)


def test_a_recovery_comment_names_and_links_the_event() -> None:
    comment = signals.recovery_comment(_recovered())
    assert comment.startswith("Recovered: infra: CI passing on dev.")
    assert LINK in comment


def test_a_recovery_comment_is_the_same_for_the_same_recovery() -> None:
    assert signals.recovery_comment(_recovered()) == signals.recovery_comment(
        _recovered()
    )


def test_a_link_that_is_not_https_is_left_out() -> None:
    comment = signals.recovery_comment(_recovered(link="javascript:alert(1)"))
    assert "javascript" not in comment


def test_a_quiet_comment_says_the_window_passed_without_the_fault() -> None:
    signal = _recovered(kind="api-fault", key="GET /v1/x 500", link=None)
    comment = signals.quiet_comment(signal, dt.timedelta(minutes=30))
    assert comment.startswith(
        "Recovered: 30 minutes passed without another GET /v1/x 500."
    )


def test_a_green_run_links_itself() -> None:
    signal = ci_signals.signal_from_workflow_run(
        {
            "action": "completed",
            "repository": {"full_name": "mini-app-polis/infra"},
            "workflow_run": {
                "status": "completed",
                "conclusion": "success",
                "name": "CI",
                "head_branch": "dev",
                "html_url": LINK,
            },
        },
        orgs=["mini-app-polis"],
        branches=["dev"],
    )
    assert signal is not None
    assert signal.state == "recovered"
    assert signal.title == "infra: CI passing on dev"
    assert signal.link == LINK


def test_a_clean_run_report_names_its_run() -> None:
    signal = run_signals.signal_from_report(
        [
            {
                "title": "db-ops · db-backup",
                "footer": {
                    "text": "SUCCESS · flow_inline · pipeline_reliability · run r9"
                },
            }
        ]
    )
    assert signal is not None
    assert signal.state == "recovered"
    assert signal.title == "db-ops: db-backup ran without an error"
    assert signal.details == "Run r9 reported SUCCESS."


@pytest.mark.asyncio
async def test_a_review_request_asks_for_review_with_the_comment() -> None:
    with patch.object(
        task_dispatch, "dispatch_task_update", AsyncMock(return_value={})
    ) as sent:
        landed = await signals._request_review(
            _recovered(), external_id="ci.x#1", comment="c", settings=get_settings()
        )
    assert landed is True
    (request,) = sent.await_args.args
    assert (request.external_id, request.comment, request.section) == (
        "ci.x#1",
        "c",
        "Review",
    )


@pytest.mark.asyncio
async def test_a_review_request_that_does_not_land_is_reported_not_raised() -> None:
    with (
        patch.object(
            task_dispatch,
            "dispatch_task_update",
            AsyncMock(side_effect=task_dispatch.DispatchError("down", detail="gone")),
        ),
        patch.object(job_queue, "report_dropped", AsyncMock()) as reported,
    ):
        landed = await signals._request_review(
            _recovered(), external_id="ci.x#1", comment="c", settings=get_settings()
        )
    assert landed is False
    reported.assert_awaited_once()
    assert reported.await_args.args == ("gone",)
