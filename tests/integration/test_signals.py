"""Signals: detected problems held as streaks, escalated only when they stick.

The engine runs against the real ``signal_streaks`` table; the queue and
GitHub are stubbed at ``task_dispatch.dispatch_task`` and
``ci_signals.remediate``. The CI detector is exercised through the
real webhook route, signature and all, the cog-run detector through
``POST /v1/notify``, and the api-fault detector through the app's own
middleware.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
import respx
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.config import get_settings
from kaianolevine_api.main import app
from kaianolevine_api.services import (
    activity,
    ci_signals,
    fault_signals,
    job_queue,
    run_signals,
    signals,
    task_dispatch,
)

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
    with (
        patch.object(
            task_dispatch,
            "dispatch_task",
            AsyncMock(return_value={"message_id": "m-1"}),
        ) as dispatched,
        patch.object(
            task_dispatch,
            "dispatch_task_update",
            AsyncMock(return_value={"message_id": "u-1"}),
        ),
    ):
        yield dispatched


@pytest.fixture
def updates(dispatch):
    """The task updates sent while ``dispatch`` is stubbed."""
    return task_dispatch.dispatch_task_update


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
    assert signal.done_when == "The CI workflow passes on dev again."
    assert signal.constraints is not None


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
    assert request.draft.effort == "S"
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


# ── recovery ──────────────────────────────────────────────────────────────

RECOVERY_LINK = "https://github.com/mini-app-polis/infra/actions/runs/2"


@pytest.mark.asyncio
async def test_an_escalated_streaks_recovery_moves_its_task_to_review(
    db_session: AsyncSession, dispatch, updates
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    outcome = await signals.observe(
        db_session,
        _signal("recovered", title="infra: CI passing on dev", link=RECOVERY_LINK),
        rule=rule,
        settings=settings,
    )

    assert outcome == "recovered"
    updates.assert_awaited_once()
    (update,) = updates.await_args.args
    assert update.external_id == dispatch.await_args.args[0].external_id
    assert update.section == "Review"
    assert "infra: CI passing on dev" in update.comment
    assert RECOVERY_LINK in update.comment


@pytest.mark.asyncio
async def test_a_streak_that_heals_before_escalating_sends_no_update(
    db_session: AsyncSession, dispatch, updates
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=2)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    outcome = await signals.observe(
        db_session, _signal("recovered"), rule=rule, settings=settings
    )

    assert outcome == "healed"
    updates.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_repeated_recovery_sends_one_update(
    db_session: AsyncSession, dispatch, updates
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    outcomes = [
        await signals.observe(
            db_session, _signal("recovered"), rule=rule, settings=settings
        )
        for _ in range(2)
    ]

    assert outcomes == ["recovered", "quiet"]
    updates.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_update_that_does_not_land_is_reported_and_the_recovery_stands(
    db_session: AsyncSession, dispatch
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    with (
        patch.object(
            task_dispatch,
            "dispatch_task_update",
            AsyncMock(
                side_effect=task_dispatch.DispatchError("down", detail="queue down")
            ),
        ),
        patch.object(job_queue, "report_dropped", AsyncMock()) as reported,
    ):
        outcome = await signals.observe(
            db_session, _signal("recovered"), rule=rule, settings=settings
        )

    assert outcome == "recovered"
    reported.assert_awaited_once()
    assert await _streaks(db_session) == [(1, 0, True, True)]


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
async def test_the_webhook_reruns_then_escalates_a_failing_main_build(
    client: AsyncClient, db_session: AsyncSession, dispatch
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(ci_signals, "remediate", AsyncMock(return_value=True)) as rerun:
        first = await _post(client, _run("failure", branch="main"))
        second = await _post(client, _run("failure", branch="main", attempt=2))

    assert first.status_code == second.status_code == 200
    rerun.assert_awaited_once()
    dispatch.assert_awaited_once()


@respx.mock
@pytest.mark.asyncio
async def test_the_webhook_does_not_watch_dev_builds(
    client: AsyncClient, db_session: AsyncSession, dispatch
) -> None:
    """dev is where work is expected to break: no streak, no re-run, no task."""
    discord = respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(ci_signals, "remediate", AsyncMock(return_value=True)) as rerun:
        first = await _post(client, _run("failure"))
        second = await _post(client, _run("failure", attempt=2))

    assert first.status_code == second.status_code == 200
    rerun.assert_not_awaited()
    dispatch.assert_not_awaited()
    assert await _streaks(db_session) == []
    assert not discord.called


@respx.mock
@pytest.mark.asyncio
async def test_outside_production_the_webhook_observes_nothing(
    client: AsyncClient, db_session: AsyncSession, dispatch, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    await _post(client, _run("failure", branch="main"))

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


# ── cog runs ──────────────────────────────────────────────────────────────


def _report(
    severity: str,
    *,
    title: str = "db-ops · db-backup",
    description: str = "pg_dump exited 1",
) -> dict:
    """A run report as mini_app_polis.pipeline_status renders one."""
    return {
        "embeds": [
            {
                "title": title,
                "description": description,
                "footer": {
                    "text": f"{severity} · flow_inline · pipeline_reliability · run r1"
                },
            }
        ],
        "username": "db-ops",
    }


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("ERROR", "failing"),
        ("CRITICAL", "failing"),
        ("WARN", "recovered"),
        ("SUCCESS", "recovered"),
        ("INFO", "recovered"),
    ],
)
def test_a_run_reports_severity_is_the_signal(severity: str, expected: str) -> None:
    signal = run_signals.signal_from_report(_report(severity)["embeds"])
    assert signal is not None
    assert signal.kind == "cog-run"
    assert signal.key == "db-ops/db-backup"
    assert signal.state == expected


def test_a_failing_run_carries_what_a_task_needs() -> None:
    signal = run_signals.signal_from_report(_report("ERROR")["embeds"])
    assert signal is not None
    assert signal.title == "db-ops: db-backup failing"
    assert signal.where == "db-ops"
    assert signal.details == "pg_dump exited 1"
    assert ("Run", "r1") in signal.sections
    assert not signal.remediable
    assert signal.done_when == "A db-backup run of db-ops finishes without an error."
    assert signal.constraints is not None


@pytest.mark.parametrize(
    "embeds",
    [
        None,
        [],
        [{"title": "evaluator-cog · conformance-check"}],
        [{"title": "no separator", "footer": {"text": "ERROR · flow_inline"}}],
        [{"title": "db-ops · db-backup", "footer": {"text": "a note · x"}}],
        _report("ERROR", title="[DEVELOPMENT] db-ops · db-backup")["embeds"],
    ],
)
def test_anything_but_a_production_run_report_says_nothing(embeds) -> None:
    assert run_signals.signal_from_report(embeds) is None


@respx.mock
@pytest.mark.asyncio
async def test_two_failing_runs_become_one_task(
    client: AsyncClient, db_session: AsyncSession, dispatch
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    for _ in range(3):
        resp = await client.post("/v1/notify", json=_report("ERROR"))
        assert resp.status_code == 200

    dispatch.assert_awaited_once()
    request = dispatch.await_args.args[0]
    assert request.source == "cog-run"
    assert request.external_id.startswith("cog-run.db-ops/db-backup#")
    assert request.draft.where == "db-ops"
    assert await _streaks(db_session) == [(3, 0, True, False)]


@respx.mock
@pytest.mark.asyncio
async def test_a_run_that_does_not_fail_closes_the_streak_quietly(
    client: AsyncClient, db_session: AsyncSession, dispatch
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    await client.post("/v1/notify", json=_report("ERROR"))
    await client.post("/v1/notify", json=_report("WARN"))
    await client.post("/v1/notify", json=_report("ERROR"))

    dispatch.assert_not_awaited()
    assert await _streaks(db_session) == [(1, 0, False, True), (1, 0, False, False)]


@respx.mock
@pytest.mark.asyncio
async def test_a_failing_run_is_seen_even_when_discord_is_down(
    client: AsyncClient, db_session: AsyncSession, dispatch
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(500))
    resp = await client.post("/v1/notify", json=_report("ERROR"))

    assert resp.status_code == 502
    assert await _streaks(db_session) == [(1, 0, False, False)]


@respx.mock
@pytest.mark.asyncio
async def test_outside_production_a_run_report_observes_nothing(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    await client.post("/v1/notify", json=_report("ERROR"))

    assert await _streaks(db_session) == []


@respx.mock
@pytest.mark.asyncio
async def test_a_signal_error_never_fails_a_run_report(client: AsyncClient) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(signals, "observe", AsyncMock(side_effect=RuntimeError("db"))):
        resp = await client.post("/v1/notify", json=_report("ERROR"))
    assert resp.status_code == 200


# ── the quiet window ──────────────────────────────────────────────────────


async def _age_streaks(session: AsyncSession, minutes: int) -> None:
    await session.execute(
        text(
            "UPDATE signal_streaks SET last_seen_at = now() - "
            "make_interval(mins => :m), opened_at = now() - make_interval(mins => :m)"
        ),
        {"m": minutes},
    )
    await session.commit()


QUIET = signals.Rule(escalate_after=3, quiet_window=dt.timedelta(minutes=30))


@pytest.mark.asyncio
async def test_a_failure_within_the_window_bumps_the_streak(
    db_session: AsyncSession, dispatch
) -> None:
    signal = _signal(kind="api-fault", key="GET /v1/x 500")
    for _ in range(2):
        await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())
    await _age_streaks(db_session, 10)
    outcome = await signals.observe(
        db_session, signal, rule=QUIET, settings=get_settings()
    )

    assert outcome == "escalated"
    assert await _streaks(db_session) == [(3, 0, True, False)]


@pytest.mark.asyncio
async def test_a_failure_after_a_quiet_window_starts_a_new_streak(
    db_session: AsyncSession, dispatch
) -> None:
    signal = _signal(kind="api-fault", key="GET /v1/x 500")
    for _ in range(2):
        await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())
    await _age_streaks(db_session, 45)
    outcome = await signals.observe(
        db_session, signal, rule=QUIET, settings=get_settings()
    )

    assert outcome == "opened"
    dispatch.assert_not_awaited()
    assert await _streaks(db_session) == [(2, 0, False, True), (1, 0, False, False)]
    closed = await db_session.execute(
        text(
            "SELECT closed_at - last_seen_at FROM signal_streaks "
            "WHERE closed_at IS NOT NULL"
        )
    )
    assert closed.scalar_one() == dt.timedelta(minutes=30)


@pytest.mark.asyncio
async def test_an_escalated_streak_closed_by_its_quiet_window_moves_to_review(
    db_session: AsyncSession, dispatch, updates
) -> None:
    signal = _signal(kind="api-fault", key="GET /v1/x 500")
    for _ in range(3):
        await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())
    await _age_streaks(db_session, 45)
    outcome = await signals.observe(
        db_session, signal, rule=QUIET, settings=get_settings()
    )

    assert outcome == "opened"
    updates.assert_awaited_once()
    (update,) = updates.await_args.args
    assert update.external_id == dispatch.await_args.args[0].external_id
    assert update.section == "Review"
    assert "30 minutes passed without another GET /v1/x 500" in update.comment


@pytest.mark.asyncio
async def test_a_quiet_window_closing_an_unescalated_streak_sends_no_update(
    db_session: AsyncSession, dispatch, updates
) -> None:
    signal = _signal(kind="api-fault", key="GET /v1/x 500")
    await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())
    await _age_streaks(db_session, 45)
    await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())

    updates.assert_not_awaited()


# ── api faults ────────────────────────────────────────────────────────────


def test_a_fault_is_keyed_by_route_template_not_url() -> None:
    signal = fault_signals.signal_for("GET", "/v1/sets/{set_id}", 500)
    assert signal.kind == "api-fault"
    assert signal.key == "GET /v1/sets/{set_id} 500"
    assert signal.where == "api-kaianolevine-com"
    assert signal.state == "failing"
    assert signal.done_when == "GET /v1/sets/{set_id} no longer answers 500."
    assert signal.constraints is not None


def test_an_escalated_task_carries_the_template_fields_the_detector_knows() -> None:
    signal = fault_signals.signal_for("GET", "/v1/sets/{set_id}", 500)
    request = signals.request_for(
        signal,
        external_id="api-fault.GET /v1/sets/{set_id} 500#1",
        failures=3,
        opened_at=dt.datetime(2026, 10, 22, 18, 0, tzinfo=dt.UTC),
    )
    assert request.draft.done_when == signal.done_when
    assert request.draft.constraints == signal.constraints
    assert request.draft.effort == "S"
    assert request.draft.where == "api-kaianolevine-com"


def test_a_request_that_matched_no_route_has_no_template() -> None:
    assert fault_signals.route_template({}) == fault_signals.UNMATCHED


async def _drain() -> None:
    for _ in range(3):
        if not activity._in_flight:
            break
        await asyncio.gather(*list(activity._in_flight), return_exceptions=True)
    await asyncio.sleep(0)


@pytest.fixture
def faulty_routes():
    """Two routes that fail on this side of the line: one answers, one raises."""

    async def unavailable(item_id: str) -> JSONResponse:
        return JSONResponse(status_code=503, content={"item": item_id})

    async def broken(item_id: str) -> None:
        raise RuntimeError("boom")

    before = list(app.router.routes)
    app.add_api_route("/v1/test-faults/unavailable/{item_id}", unavailable)
    app.add_api_route("/v1/test-faults/broken/{item_id}", broken)
    yield
    app.router.routes[:] = before


@pytest.fixture
async def raw_client(client: AsyncClient):
    """The app without the client re-raising its unhandled exceptions."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@respx.mock
@pytest.mark.asyncio
async def test_repeated_5xx_from_one_route_become_one_task(
    raw_client: AsyncClient, db_session: AsyncSession, dispatch, faulty_routes
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    for item in ("a", "b", "c", "d"):
        resp = await raw_client.get(f"/v1/test-faults/unavailable/{item}")
        assert resp.status_code == 503
        await _drain()

    dispatch.assert_awaited_once()
    request = dispatch.await_args.args[0]
    assert request.source == "api-fault"
    assert request.external_id.startswith(
        "api-fault.GET /v1/test-faults/unavailable/{item_id} 503#"
    )
    assert await _streaks(db_session) == [(4, 0, True, False)]


@respx.mock
@pytest.mark.asyncio
async def test_an_unhandled_exception_is_a_500_fault(
    raw_client: AsyncClient, db_session: AsyncSession, dispatch, faulty_routes
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    resp = await raw_client.get("/v1/test-faults/broken/a")
    await _drain()

    assert resp.status_code == 500
    key = (await db_session.execute(text("SELECT key FROM signal_streaks"))).scalar()
    assert key == "GET /v1/test-faults/broken/{item_id} 500"


@respx.mock
@pytest.mark.asyncio
async def test_outside_production_a_fault_observes_nothing(
    raw_client: AsyncClient, db_session: AsyncSession, faulty_routes, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    await raw_client.get("/v1/test-faults/unavailable/a")
    await _drain()

    assert await _streaks(db_session) == []


@respx.mock
@pytest.mark.asyncio
async def test_a_signal_error_never_changes_the_answer(
    raw_client: AsyncClient, faulty_routes
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(signals, "observe", AsyncMock(side_effect=RuntimeError("db"))):
        resp = await raw_client.get("/v1/test-faults/unavailable/a")
        await _drain()
    assert resp.status_code == 503
