"""Signals: detected problems held as streaks, escalated only when they stick.

The engine runs against the real ``signal_streaks`` table; the queue and
GitHub are stubbed at ``task_dispatch.dispatch_task`` and
``ci_signals.remediate``. The CI detector is exercised through the
real webhook route, signature and all.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import ci_signals, job_queue, signals, task_dispatch

SECRET = "test-github-secret"
DISCORD_URL = "https://discord.test/api/webhooks/1/token"

ORGS = ["mini-app-polis"]
BRANCHES = ["main", "dev"]


def _run(
    conclusion: str,
    *,
    branch: str = "dev",
    attempt: int = 1,
    full_name: str = "mini-app-polis/infra",
    run_id: int = 99,
) -> dict:
    return {
        "action": "completed",
        "repository": {
            "full_name": full_name,
            "html_url": f"https://github.com/{full_name}",
            "default_branch": "main",
        },
        "workflow_run": {
            "id": run_id,
            "name": "CI",
            "status": "completed",
            "conclusion": conclusion,
            "head_branch": branch,
            "head_sha": "c" * 40,
            "head_commit": {"message": "fix: a change\n\nbody"},
            "html_url": f"https://github.com/{full_name}/actions/runs/{run_id}",
            "run_number": 7,
            "run_attempt": attempt,
            "actor": {"login": "kaianolevine"},
            "updated_at": "2026-10-21T12:00:00Z",
        },
    }


def _signal(state: str = "failing", **overrides) -> signals.Signal:
    base = {
        "kind": "ci",
        "key": "mini-app-polis/infra/CI@dev",
        "state": state,
        "title": "infra: CI failing on dev",
        "where": "infra",
        "link": "https://github.com/mini-app-polis/infra/actions/runs/1",
        "details": "failure on ccccccc: fix: a change",
        "remediable": True,
    }
    base.update(overrides)
    return signals.Signal(**base)


async def _streaks(session: AsyncSession) -> list[tuple]:
    rows = await session.execute(
        text(
            "SELECT failures, remediations, escalated_at IS NOT NULL, "
            "closed_at IS NOT NULL FROM signal_streaks ORDER BY opened_at"
        )
    )
    return [tuple(r) for r in rows]


@pytest.fixture
def dispatch():
    with patch.object(
        task_dispatch, "dispatch_task", AsyncMock(return_value={"message_id": "m-1"})
    ) as dispatched:
        yield dispatched


# ── the detector ──────────────────────────────────────────────────────────


def test_a_failed_run_is_a_remediable_failing_signal() -> None:
    signal = ci_signals.signal_from_workflow_run(
        _run("failure"), orgs=ORGS, branches=BRANCHES
    )
    assert signal is not None
    assert signal.state == "failing"
    assert signal.key == "mini-app-polis/infra/CI@dev"
    assert signal.title == "infra: CI failing on dev"
    assert signal.remediable is True
    assert signal.context == {"repo": "mini-app-polis/infra", "run_id": 99}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (_run("success"), "recovered"),
        (_run("timed_out"), "failing"),
        (_run("action_required"), "failing"),
        (_run("cancelled"), None),
        (_run("skipped"), None),
        (_run("failure", branch="feature/x"), None),
        (_run("failure", full_name="kaianolevine/example"), None),
        ({**_run("failure"), "action": "requested"}, None),
    ],
)
def test_what_each_run_says(payload: dict, expected: str | None) -> None:
    signal = ci_signals.signal_from_workflow_run(payload, orgs=ORGS, branches=BRANCHES)
    assert (signal.state if signal else None) == expected


def test_a_rerun_and_an_approval_are_never_re_run() -> None:
    rerun = ci_signals.signal_from_workflow_run(
        _run("failure", attempt=2), orgs=ORGS, branches=BRANCHES
    )
    approval = ci_signals.signal_from_workflow_run(
        _run("action_required"), orgs=ORGS, branches=BRANCHES
    )
    assert rerun is not None and rerun.remediable is False
    assert approval is not None and approval.remediable is False


# ── the engine ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failure_that_heals_after_its_rerun_never_reaches_the_board(
    db_session: AsyncSession, dispatch
) -> None:
    settings = get_settings()
    rule = signals.Rule(
        escalate_after=2, remediation=AsyncMock(return_value=True), max_remediations=1
    )
    first = await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    healed = await signals.observe(
        db_session, _signal("recovered"), rule=rule, settings=settings
    )

    assert (first, healed) == ("remediating", "healed")
    rule.remediation.assert_awaited_once()
    dispatch.assert_not_awaited()
    assert await _streaks(db_session) == [(1, 1, False, True)]


@pytest.mark.asyncio
async def test_a_failure_that_survives_its_rerun_becomes_one_task(
    db_session: AsyncSession, dispatch
) -> None:
    settings = get_settings()
    rule = signals.Rule(
        escalate_after=2, remediation=AsyncMock(return_value=True), max_remediations=1
    )
    outcomes = [
        await signals.observe(db_session, _signal(), rule=rule, settings=settings),
        await signals.observe(
            db_session, _signal(remediable=False), rule=rule, settings=settings
        ),
        await signals.observe(
            db_session, _signal(remediable=False), rule=rule, settings=settings
        ),
    ]

    assert outcomes == ["remediating", "escalated", "bumped"]
    dispatch.assert_awaited_once()
    (request,) = dispatch.await_args.args
    assert request.source == "ci"
    assert request.external_id.startswith("ci.mini-app-polis/infra/CI@dev#")
    assert request.draft.title == "infra: CI failing on dev"
    assert any(s.label == "Streak" for s in request.draft.sections)
    assert await _streaks(db_session) == [(3, 1, True, False)]


@pytest.mark.asyncio
async def test_a_recovery_closes_the_streak_and_the_next_failure_starts_afresh(
    db_session: AsyncSession, dispatch
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    recovered = await signals.observe(
        db_session, _signal("recovered"), rule=rule, settings=settings
    )
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)

    assert recovered == "recovered"
    first, second = (call.args[0] for call in dispatch.await_args_list)
    assert first.external_id != second.external_id
    assert await _streaks(db_session) == [(1, 0, True, True), (1, 0, True, False)]


@pytest.mark.asyncio
async def test_a_remediation_that_cannot_start_escalates_at_once(
    db_session: AsyncSession, dispatch
) -> None:
    rule = signals.Rule(
        escalate_after=2, remediation=AsyncMock(return_value=False), max_remediations=1
    )
    outcome = await signals.observe(
        db_session, _signal(), rule=rule, settings=get_settings()
    )

    assert outcome == "escalated"
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_dropped_request_is_reported_and_retried_by_the_next_failure(
    db_session: AsyncSession,
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    with (
        patch.object(
            task_dispatch,
            "dispatch_task",
            AsyncMock(
                side_effect=[
                    task_dispatch.DispatchError("down", detail="queue down"),
                    {"message_id": "m-2"},
                ]
            ),
        ) as dispatched,
        patch.object(job_queue, "report_dropped", AsyncMock()) as reported,
    ):
        first = await signals.observe(
            db_session, _signal(), rule=rule, settings=settings
        )
        second = await signals.observe(
            db_session, _signal(), rule=rule, settings=settings
        )

    assert (first, second) == ("dropped", "escalated")
    reported.assert_awaited_once()
    assert dispatched.await_count == 2
    assert await _streaks(db_session) == [(2, 0, True, False)]


@pytest.mark.asyncio
async def test_a_recovery_with_no_streak_is_quiet(db_session: AsyncSession) -> None:
    outcome = await signals.observe(
        db_session,
        _signal("recovered"),
        rule=signals.Rule(),
        settings=get_settings(),
    )
    assert outcome == "quiet"
    assert await _streaks(db_session) == []


# ── through the webhook ───────────────────────────────────────────────────


async def _post(client: AsyncClient, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return await client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-GitHub-Delivery": "d-1",
            "X-Hub-Signature-256": f"sha256={digest}",
            "Content-Type": "application/json",
        },
    )


@respx.mock
@pytest.mark.asyncio
async def test_the_webhook_reruns_then_escalates_a_failing_dev_build(
    client: AsyncClient, db_session: AsyncSession, dispatch
) -> None:
    """dev is not announced on Discord, and is still watched for problems."""
    discord = respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(ci_signals, "remediate", AsyncMock(return_value=True)) as rerun:
        first = await _post(client, _run("failure"))
        second = await _post(client, _run("failure", attempt=2))

    assert first.status_code == second.status_code == 200
    rerun.assert_awaited_once()
    dispatch.assert_awaited_once()
    assert not discord.called


@respx.mock
@pytest.mark.asyncio
async def test_outside_production_the_webhook_observes_nothing(
    client: AsyncClient, db_session: AsyncSession, dispatch, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    await _post(client, _run("failure"))

    dispatch.assert_not_awaited()
    assert await _streaks(db_session) == []


@respx.mock
@pytest.mark.asyncio
async def test_a_signal_error_never_fails_the_delivery(
    client: AsyncClient, monkeypatch
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(signals, "observe", AsyncMock(side_effect=RuntimeError("db"))):
        resp = await _post(client, _run("failure"))
    assert resp.status_code == 200


# ── the re-run call ───────────────────────────────────────────────────────


GH = "https://api.github.com/repos/mini-app-polis"
JOBS = f"{GH}/infra/actions/runs/99/jobs"
RERUN = f"{GH}/infra/actions/runs/99/rerun-failed-jobs"
DISPATCH = f"{GH}/.github/actions/workflows/dependency-fix.yml/dispatches"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "gh-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _jobs(*failed: str) -> Response:
    jobs = [{"name": n, "conclusion": "failure"} for n in failed]
    jobs.append({"name": "test / test", "conclusion": "success"})
    return Response(200, json={"jobs": jobs})


def _failing_signal() -> signals.Signal:
    signal = ci_signals.signal_from_workflow_run(
        _run("failure"), orgs=ORGS, branches=BRANCHES
    )
    assert signal is not None
    return signal


@respx.mock
@pytest.mark.asyncio
async def test_a_failed_build_is_re_run(token) -> None:
    respx.get(url__startswith=JOBS).mock(return_value=_jobs("test / test"))
    rerun = respx.post(RERUN).mock(return_value=Response(201))
    dispatch = respx.post(DISPATCH).mock(return_value=Response(204))

    assert await ci_signals.remediate(_failing_signal(), get_settings()) is True
    assert rerun.called and not dispatch.called
    assert rerun.calls.last.request.headers["Authorization"] == "Bearer gh-test"


@respx.mock
@pytest.mark.asyncio
async def test_a_failed_dependency_audit_dispatches_the_fix_instead(token) -> None:
    """A re-run would fail the same way; the lock needs re-locking."""
    respx.get(url__startswith=JOBS).mock(
        return_value=_jobs("security / dependency audit (python)")
    )
    rerun = respx.post(RERUN).mock(return_value=Response(201))
    dispatch = respx.post(DISPATCH).mock(return_value=Response(204))

    assert await ci_signals.remediate(_failing_signal(), get_settings()) is True
    assert dispatch.called and not rerun.called
    assert json.loads(dispatch.calls.last.request.content) == {
        "ref": "main",
        "inputs": {"repository": "infra"},
    }


@respx.mock
@pytest.mark.asyncio
async def test_when_the_jobs_cannot_be_read_the_build_is_re_run(token) -> None:
    respx.get(url__startswith=JOBS).mock(return_value=Response(502))
    rerun = respx.post(RERUN).mock(return_value=Response(201))

    assert await ci_signals.remediate(_failing_signal(), get_settings()) is True
    assert rerun.called


@respx.mock
@pytest.mark.asyncio
async def test_a_refused_remedy_reports_that_none_started(token) -> None:
    respx.get(url__startswith=JOBS).mock(
        return_value=_jobs("security / dependency audit (python)")
    )
    respx.post(DISPATCH).mock(return_value=Response(403))

    assert await ci_signals.remediate(_failing_signal(), get_settings()) is False


@pytest.mark.asyncio
async def test_without_a_token_no_remedy_starts(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS_TOKEN", raising=False)
    get_settings.cache_clear()
    assert await ci_signals.remediate(_failing_signal(), get_settings()) is False
    get_settings.cache_clear()
