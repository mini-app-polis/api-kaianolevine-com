"""Signals: detected problems held as streaks, escalated only when they stick.

The engine runs against the real ``signal_streaks`` table; GitHub is stubbed
at the Issue writer (``github_issues``) and ``ci_signals.remediate``, and the
queue at ``task_dispatch``. The CI detector is exercised through the
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
from types import SimpleNamespace
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
    github_issues,
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


def _opened(repo: str, **_: object) -> github_issues.Issue:
    return github_issues.Issue(repo, 7, f"https://github.com/{repo}/issues/7")


@pytest.fixture
def issues():
    """The Issue writer and the task queue, stubbed: what each was asked."""
    with (
        patch.object(
            github_issues, "open_issue", AsyncMock(side_effect=_opened)
        ) as opened,
        patch.object(github_issues, "comment", AsyncMock()) as commented,
        patch.object(github_issues, "close", AsyncMock()) as closed,
        patch.object(github_issues, "is_open", AsyncMock(return_value=True)) as is_open,
        patch.object(
            task_dispatch,
            "dispatch_task",
            AsyncMock(return_value={"message_id": "m-1"}),
        ) as tasked,
        patch.object(
            task_dispatch,
            "dispatch_task_update",
            AsyncMock(return_value={"message_id": "u-1"}),
        ) as updated,
    ):
        yield SimpleNamespace(
            open=opened,
            comment=commented,
            close=closed,
            is_open=is_open,
            task=tasked,
            update=updated,
        )


async def _escalated_task_streak(session: AsyncSession, external_id: str) -> None:
    """A streak escalated as a board task, before Issues."""
    await session.execute(
        text(
            "INSERT INTO signal_streaks (kind, key, title, escalated_at, "
            "task_external_id) VALUES ('ci', 'mini-app-polis/infra/CI@dev', "
            "'infra: CI failing on dev', now(), :external_id)"
        ),
        {"external_id": external_id},
    )
    await session.commit()


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

REPO = "mini-app-polis/infra"


@pytest.mark.asyncio
async def test_a_failure_that_heals_after_its_rerun_is_never_reported(
    db_session: AsyncSession, issues
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
    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == [(1, 1, False, True)]


@pytest.mark.asyncio
async def test_a_failure_that_survives_its_rerun_becomes_one_issue(
    db_session: AsyncSession, issues
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
    issues.open.assert_awaited_once()
    (repo,) = issues.open.await_args.args
    opened = issues.open.await_args.kwargs
    assert repo == REPO
    assert opened["marker"] == "ci/mini-app-polis/infra/CI@dev"
    assert opened["title"] == "infra: CI failing on dev"
    assert "**Streak:** 2 failure(s)" in opened["body"]
    issues.task.assert_not_awaited()
    assert await _streaks(db_session) == [(3, 1, True, False)]
    stored = await db_session.execute(
        text("SELECT issue_repo, issue_number FROM signal_streaks")
    )
    assert tuple(stored.one()) == (REPO, 7)


@pytest.mark.asyncio
async def test_each_further_failure_comments_on_the_issue(
    db_session: AsyncSession, issues
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)

    issues.comment.assert_awaited_once()
    repo, number, body = issues.comment.await_args.args
    assert (repo, number) == (REPO, 7)
    assert body.startswith("Failed again: 2 failure(s)")


@pytest.mark.asyncio
async def test_a_comment_that_does_not_land_still_bumps_the_streak(
    db_session: AsyncSession, issues
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    issues.comment.side_effect = github_issues.IssueError("refused")
    outcome = await signals.observe(db_session, _signal(), rule=rule, settings=settings)

    assert outcome == "bumped"
    assert await _streaks(db_session) == [(2, 0, True, False)]


@pytest.mark.asyncio
async def test_a_recovery_closes_the_streak_and_the_next_failure_starts_afresh(
    db_session: AsyncSession, issues
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    recovered = await signals.observe(
        db_session, _signal("recovered"), rule=rule, settings=settings
    )
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)

    assert recovered == "recovered"
    assert issues.open.await_count == 2
    issues.close.assert_awaited_once()
    assert await _streaks(db_session) == [(1, 0, True, True), (1, 0, True, False)]


@pytest.mark.asyncio
async def test_a_remediation_that_cannot_start_escalates_at_once(
    db_session: AsyncSession, issues
) -> None:
    rule = signals.Rule(
        escalate_after=2, remediation=AsyncMock(return_value=False), max_remediations=1
    )
    outcome = await signals.observe(
        db_session, _signal(), rule=rule, settings=get_settings()
    )

    assert outcome == "escalated"
    issues.open.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_issue_that_cannot_be_opened_is_reported_and_retried(
    db_session: AsyncSession, issues
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    issues.open.side_effect = [
        github_issues.IssueError("the signals GitHub App is not configured"),
        github_issues.Issue(REPO, 8, f"https://github.com/{REPO}/issues/8"),
    ]
    with patch.object(job_queue, "report_dropped", AsyncMock()) as reported:
        first = await signals.observe(
            db_session, _signal(), rule=rule, settings=settings
        )
        second = await signals.observe(
            db_session, _signal(), rule=rule, settings=settings
        )

    assert (first, second) == ("dropped", "escalated")
    reported.assert_awaited_once()
    assert "not configured" in reported.await_args.args[0]
    assert issues.open.await_count == 2
    assert await _streaks(db_session) == [(2, 0, True, False)]


@pytest.mark.asyncio
async def test_a_signal_that_names_no_repository_is_reported(
    db_session: AsyncSession, issues
) -> None:
    with patch.object(job_queue, "report_dropped", AsyncMock()) as reported:
        outcome = await signals.observe(
            db_session,
            _signal(where=None),
            rule=signals.Rule(escalate_after=1),
            settings=get_settings(),
        )

    assert outcome == "dropped"
    issues.open.assert_not_awaited()
    reported.assert_awaited_once()


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
async def test_an_escalated_streaks_recovery_closes_its_issue(
    db_session: AsyncSession, issues
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
    issues.close.assert_awaited_once()
    assert issues.close.await_args.args == (REPO, 7)
    body = issues.close.await_args.kwargs["body"]
    assert body.startswith("Recovered: infra: CI passing on dev.")
    assert RECOVERY_LINK in body
    issues.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_streak_escalated_as_a_task_still_moves_it_to_review(
    db_session: AsyncSession, issues
) -> None:
    await _escalated_task_streak(db_session, "ci.mini-app-polis/infra/CI@dev#1")
    outcome = await signals.observe(
        db_session,
        _signal("recovered", title="infra: CI passing on dev", link=RECOVERY_LINK),
        rule=signals.Rule(escalate_after=1),
        settings=get_settings(),
    )

    assert outcome == "recovered"
    issues.close.assert_not_awaited()
    issues.update.assert_awaited_once()
    (update,) = issues.update.await_args.args
    assert update.external_id == "ci.mini-app-polis/infra/CI@dev#1"
    assert update.section == "Review"
    assert "infra: CI passing on dev" in update.comment
    assert RECOVERY_LINK in update.comment


@pytest.mark.asyncio
async def test_a_streak_that_heals_before_escalating_sends_nothing(
    db_session: AsyncSession, issues
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=2)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    outcome = await signals.observe(
        db_session, _signal("recovered"), rule=rule, settings=settings
    )

    assert outcome == "healed"
    issues.close.assert_not_awaited()
    issues.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_repeated_recovery_closes_once(
    db_session: AsyncSession, issues
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
    issues.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_close_that_does_not_land_is_reported_and_the_recovery_stands(
    db_session: AsyncSession, issues
) -> None:
    settings = get_settings()
    rule = signals.Rule(escalate_after=1)
    await signals.observe(db_session, _signal(), rule=rule, settings=settings)
    issues.close.side_effect = github_issues.IssueError("refused")
    with patch.object(job_queue, "report_dropped", AsyncMock()) as reported:
        outcome = await signals.observe(
            db_session, _signal("recovered"), rule=rule, settings=settings
        )

    assert outcome == "recovered"
    reported.assert_awaited_once()
    assert await _streaks(db_session) == [(1, 0, True, True)]


@pytest.mark.asyncio
async def test_an_old_tasks_update_that_does_not_land_is_reported(
    db_session: AsyncSession, issues
) -> None:
    await _escalated_task_streak(db_session, "ci.mini-app-polis/infra/CI@dev#1")
    issues.update.side_effect = task_dispatch.DispatchError("down", detail="queue down")
    with patch.object(job_queue, "report_dropped", AsyncMock()) as reported:
        outcome = await signals.observe(
            db_session,
            _signal("recovered"),
            rule=signals.Rule(),
            settings=get_settings(),
        )

    assert outcome == "recovered"
    reported.assert_awaited_once()


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
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(ci_signals, "remediate", AsyncMock(return_value=True)) as rerun:
        first = await _post(client, _run("failure", branch="main"))
        second = await _post(client, _run("failure", branch="main", attempt=2))

    assert first.status_code == second.status_code == 200
    rerun.assert_awaited_once()
    issues.open.assert_awaited_once()
    assert issues.open.await_args.args == ("mini-app-polis/infra",)


@respx.mock
@pytest.mark.asyncio
async def test_the_webhook_does_not_watch_dev_builds(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    """dev is where work is expected to break: no streak, no re-run, no Issue."""
    discord = respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(ci_signals, "remediate", AsyncMock(return_value=True)) as rerun:
        first = await _post(client, _run("failure"))
        second = await _post(client, _run("failure", attempt=2))

    assert first.status_code == second.status_code == 200
    rerun.assert_not_awaited()
    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == []
    assert not discord.called


@respx.mock
@pytest.mark.asyncio
async def test_outside_production_the_webhook_observes_nothing(
    client: AsyncClient, db_session: AsyncSession, issues, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    await _post(client, _run("failure", branch="main"))

    issues.open.assert_not_awaited()
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


# ── a failed audit off the signal branches ────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_a_failed_audit_on_dev_dispatches_the_fix_without_a_signal(
    client: AsyncClient, db_session: AsyncSession, issues, token
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    respx.get(url__startswith=JOBS).mock(
        return_value=_jobs("security / dependency audit (python)")
    )
    dispatch = respx.post(DISPATCH).mock(return_value=Response(204))

    resp = await _post(client, _run("failure", branch="dev"))

    assert resp.status_code == 200
    assert json.loads(dispatch.calls.last.request.content) == {
        "ref": "main",
        "inputs": {"repository": "infra"},
    }
    issues.open.assert_not_called()


@respx.mock
@pytest.mark.asyncio
async def test_other_failures_off_the_signal_branches_start_nothing(
    client: AsyncClient, token
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    respx.get(url__startswith=JOBS).mock(return_value=_jobs("test / test"))
    dispatch = respx.post(DISPATCH).mock(return_value=Response(204))

    await _post(
        client, _run("failure", branch="commons-update/miniapppolis-common-utils")
    )

    assert not dispatch.called


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(("branch", "attempt"), [("security-fix/deps", 1), ("dev", 2)])
async def test_the_fixers_own_branch_and_re_runs_are_not_dispatched(
    client: AsyncClient, token, branch: str, attempt: int
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    jobs = respx.get(url__startswith=JOBS).mock(
        return_value=_jobs("security / dependency audit (python)")
    )
    dispatch = respx.post(DISPATCH).mock(return_value=Response(204))

    await _post(client, _run("failure", branch=branch, attempt=attempt))

    assert not jobs.called and not dispatch.called


@respx.mock
@pytest.mark.asyncio
async def test_a_dispatch_error_never_fails_the_delivery(
    client: AsyncClient, token
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    with patch.object(
        ci_signals,
        "fix_audit_off_signal_branches",
        AsyncMock(side_effect=RuntimeError("github")),
    ):
        resp = await _post(client, _run("failure", branch="dev"))
    assert resp.status_code == 200


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
                "footer": {"text": f"{severity} · run r1"},
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


def test_an_older_footer_still_names_the_run() -> None:
    """Before common-python-utils 7, the footer carried more fields."""
    embeds = _report("ERROR")["embeds"]
    embeds[0]["footer"]["text"] = "ERROR · flow_inline · pipeline_reliability · run r2"
    signal = run_signals.signal_from_report(embeds)
    assert signal is not None
    assert ("Run", "r2") in signal.sections


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
async def test_two_failing_runs_become_one_issue(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    for _ in range(3):
        resp = await client.post("/v1/notify", json=_report("ERROR"))
        assert resp.status_code == 200

    issues.open.assert_awaited_once()
    assert issues.open.await_args.args == ("mini-app-polis/db-ops",)
    assert issues.open.await_args.kwargs["marker"] == "cog-run/db-ops/db-backup"
    issues.task.assert_not_awaited()
    assert await _streaks(db_session) == [(3, 0, True, False)]


@respx.mock
@pytest.mark.asyncio
async def test_a_run_that_does_not_fail_closes_the_streak_quietly(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    await client.post("/v1/notify", json=_report("ERROR"))
    await client.post("/v1/notify", json=_report("WARN"))
    await client.post("/v1/notify", json=_report("ERROR"))

    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == [(1, 0, False, True), (1, 0, False, False)]


@respx.mock
@pytest.mark.asyncio
async def test_a_failing_run_is_seen_even_when_discord_is_down(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(500))
    resp = await client.post("/v1/notify", json=_report("ERROR"))

    assert resp.json()["data"]["reason"] == "queued"
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
    db_session: AsyncSession, issues
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
    db_session: AsyncSession, issues
) -> None:
    signal = _signal(kind="api-fault", key="GET /v1/x 500")
    for _ in range(2):
        await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())
    await _age_streaks(db_session, 45)
    outcome = await signals.observe(
        db_session, signal, rule=QUIET, settings=get_settings()
    )

    assert outcome == "opened"
    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == [(2, 0, False, True), (1, 0, False, False)]
    closed = await db_session.execute(
        text(
            "SELECT closed_at - last_seen_at FROM signal_streaks "
            "WHERE closed_at IS NOT NULL"
        )
    )
    assert closed.scalar_one() == dt.timedelta(minutes=30)


@pytest.mark.asyncio
async def test_an_escalated_streak_closed_by_its_quiet_window_closes_its_issue(
    db_session: AsyncSession, issues
) -> None:
    signal = _signal(kind="api-fault", key="GET /v1/x 500")
    for _ in range(3):
        await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())
    await _age_streaks(db_session, 45)
    outcome = await signals.observe(
        db_session, signal, rule=QUIET, settings=get_settings()
    )

    assert outcome == "opened"
    issues.close.assert_awaited_once()
    body = issues.close.await_args.kwargs["body"]
    assert body == "Recovered: 30 minutes passed without another GET /v1/x 500."
    issues.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_quiet_window_closing_an_unescalated_streak_sends_nothing(
    db_session: AsyncSession, issues
) -> None:
    signal = _signal(kind="api-fault", key="GET /v1/x 500")
    await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())
    await _age_streaks(db_session, 45)
    await signals.observe(db_session, signal, rule=QUIET, settings=get_settings())

    issues.close.assert_not_awaited()
    issues.update.assert_not_awaited()


# ── api faults ────────────────────────────────────────────────────────────


def test_a_fault_is_keyed_by_route_template_not_url() -> None:
    signal = fault_signals.signal_for("GET", "/v1/sets/{set_id}", 500, quiet_hours=24)
    assert signal.kind == "api-fault"
    assert signal.key == "GET /v1/sets/{set_id} 500"
    assert signal.where == "api-kaianolevine-com"
    assert signal.state == "failing"
    assert signal.done_when is not None
    assert signal.constraints is not None


def test_an_escalated_issue_carries_what_the_detector_knows() -> None:
    signal = fault_signals.signal_for("GET", "/v1/sets/{set_id}", 500, quiet_hours=24)
    body = signals.issue_body(
        signal,
        failures=3,
        opened_at=dt.datetime(2026, 10, 22, 18, 0, tzinfo=dt.UTC),
    )
    assert f"**Done when:** {signal.done_when}" in body
    assert f"**Constraints:** {signal.constraints}" in body
    assert "**Streak:** 3 failure(s) since 2026-10-22 18:00 UTC" in body
    assert (
        signals.issue_repo_for(signal, get_settings())
        == "mini-app-polis/api-kaianolevine-com"
    )


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
async def test_repeated_5xx_from_one_route_become_one_issue(
    raw_client: AsyncClient, db_session: AsyncSession, issues, faulty_routes
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    for item in ("a", "b", "c", "d"):
        resp = await raw_client.get(f"/v1/test-faults/unavailable/{item}")
        assert resp.status_code == 503
        await _drain()

    issues.open.assert_awaited_once()
    assert issues.open.await_args.args == ("mini-app-polis/api-kaianolevine-com",)
    assert (
        issues.open.await_args.kwargs["marker"]
        == "api-fault/GET /v1/test-faults/unavailable/{item_id} 503"
    )
    # A dependency fault's Issue gets no opening comment: three, one a fault.
    assert issues.comment.await_count == 3
    assert await _streaks(db_session) == [(4, 0, True, False)]


@respx.mock
@pytest.mark.asyncio
async def test_an_unhandled_exception_is_a_500_fault(
    raw_client: AsyncClient, db_session: AsyncSession, issues, faulty_routes
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


# ── every 5xx an Issue: 500s by a fix, 502-504 by a quiet day ────────────

FAULT_REPO = "mini-app-polis/api-kaianolevine-com"


def _fault(status: int, **kwargs) -> signals.Signal:
    return fault_signals.signal_for(
        "GET", "/v1/x/{id}", status, quiet_hours=24, **kwargs
    )


async def _observe_fault(db_session: AsyncSession, signal: signals.Signal) -> str:
    status = signal.context["status"]
    return await signals.observe(
        db_session,
        signal,
        rule=fault_signals.rule(status, get_settings()),
        settings=get_settings(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_the_first_fault_opens_an_issue(
    db_session: AsyncSession, issues, status: int
) -> None:
    assert await _observe_fault(db_session, _fault(status)) == "escalated"
    issues.open.assert_awaited_once()
    assert issues.open.await_args.args == (FAULT_REPO,)
    assert await _streaks(db_session) == [(1, 0, True, False)]


@pytest.mark.asyncio
async def test_a_500_issue_is_told_to_close_by_a_fixing_pull_request(
    db_session: AsyncSession, issues
) -> None:
    await _observe_fault(db_session, _fault(500))

    issues.comment.assert_awaited_once()
    repo, number, body = issues.comment.await_args.args
    assert (repo, number) == (FAULT_REPO, 7)
    assert "`Fixes #7`" in body


@pytest.mark.asyncio
async def test_each_further_fault_comments_with_its_particulars(
    db_session: AsyncSession, issues
) -> None:
    await _observe_fault(db_session, _fault(503))
    outcome = await _observe_fault(
        db_session, _fault(503, request_id="req-2", sentry_event_id="evt-2")
    )

    assert outcome == "bumped"
    body = issues.comment.await_args.args[2]
    assert body.startswith("Failed again: 2 failure(s) in this streak.")
    assert "**Request id:** req-2" in body
    assert "**Sentry event:** evt-2" in body
    assert "**At:** " in body


@pytest.mark.asyncio
async def test_a_500_recurring_after_its_issue_closed_opens_one_linking_it(
    db_session: AsyncSession, issues
) -> None:
    await _observe_fault(db_session, _fault(500))
    issues.is_open.return_value = False
    issues.open.side_effect = lambda repo, **_: github_issues.Issue(
        repo, 8, f"https://github.com/{repo}/issues/8"
    )

    outcome = await _observe_fault(db_session, _fault(500))

    assert outcome == "escalated"
    issues.is_open.assert_awaited_once_with(FAULT_REPO, 7, settings=get_settings())
    assert "**Recurs:** #7 was closed" in issues.open.await_args.kwargs["body"]
    assert await _streaks(db_session) == [(1, 0, True, True), (1, 0, True, False)]
    issue = await db_session.execute(
        text("SELECT issue_number FROM signal_streaks WHERE closed_at IS NULL")
    )
    assert issue.scalar_one() == 8


@pytest.mark.asyncio
async def test_a_500_with_its_issue_still_open_only_comments(
    db_session: AsyncSession, issues
) -> None:
    await _observe_fault(db_session, _fault(500))
    assert await _observe_fault(db_session, _fault(500)) == "bumped"
    issues.open.assert_awaited_once()


@pytest.mark.asyncio
async def test_github_unable_to_say_leaves_the_issue_open(
    db_session: AsyncSession, issues
) -> None:
    await _observe_fault(db_session, _fault(500))
    issues.is_open.side_effect = github_issues.IssueError("down")
    assert await _observe_fault(db_session, _fault(500)) == "bumped"
    issues.open.assert_awaited_once()


async def _age_hours(db_session: AsyncSession, hours: int) -> None:
    await _age_streaks(db_session, hours * 60)


@pytest.mark.asyncio
async def test_the_sweep_closes_a_dependency_fault_quiet_for_a_day(
    db_session: AsyncSession, issues
) -> None:
    await _observe_fault(db_session, _fault(503))
    await _age_hours(db_session, 25)

    result = await fault_signals.sweep_quiet(get_settings())

    assert result == fault_signals.SweepResult(quiet=1, closed=1)
    issues.close.assert_awaited_once()
    assert issues.close.await_args.args == (FAULT_REPO, 7)
    assert issues.close.await_args.kwargs["body"] == (
        "Recovered: 24 hours passed without another GET /v1/x/{id} 503."
    )
    assert await _streaks(db_session) == [(1, 0, True, True)]


@pytest.mark.asyncio
async def test_the_sweep_leaves_a_dependency_fault_inside_its_day(
    db_session: AsyncSession, issues
) -> None:
    await _observe_fault(db_session, _fault(502))
    await _age_hours(db_session, 23)

    assert await fault_signals.sweep_quiet(get_settings()) == (
        fault_signals.SweepResult(quiet=0, closed=0)
    )
    issues.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_sweep_never_closes_a_500(db_session: AsyncSession, issues) -> None:
    await _observe_fault(db_session, _fault(500))
    await _age_hours(db_session, 72)

    assert await fault_signals.sweep_quiet(get_settings()) == (
        fault_signals.SweepResult(quiet=0, closed=0)
    )
    issues.close.assert_not_awaited()
    assert await _streaks(db_session) == [(1, 0, True, False)]


@pytest.mark.asyncio
async def test_a_dependency_fault_after_a_quiet_day_opens_a_new_issue(
    db_session: AsyncSession, issues
) -> None:
    await _observe_fault(db_session, _fault(504))
    await _age_hours(db_session, 25)

    assert await _observe_fault(db_session, _fault(504)) == "escalated"
    issues.close.assert_awaited_once()
    assert issues.open.await_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_the_middleware_names_the_request_and_sentry_event(
    raw_client: AsyncClient, db_session: AsyncSession, issues, faulty_routes
) -> None:
    respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))
    await raw_client.get(
        "/v1/test-faults/unavailable/a", headers={"X-Request-Id": "req-a"}
    )
    await _drain()
    with patch.object(activity.sentry_sdk, "capture_exception", return_value="ev-b"):
        await raw_client.get("/v1/test-faults/broken/b", headers={"CF-Ray": "8f00-ORD"})
        await _drain()

    first = issues.open.await_args_list[0].kwargs["body"]
    assert "**Request id:** req-a" in first
    second = issues.open.await_args_list[1].kwargs["body"]
    assert "**Request id:** 8f00-ORD" in second
    assert "**Sentry event:** ev-b" in second


@pytest.mark.asyncio
async def test_the_quiet_sweep_route_runs_the_sweep(client: AsyncClient) -> None:
    with patch.object(
        fault_signals,
        "sweep_quiet",
        AsyncMock(return_value=fault_signals.SweepResult(quiet=2, closed=1)),
    ) as swept:
        response = await client.post("/v1/signals/api-faults/quiet-sweep")

    assert response.status_code == 200
    assert response.json()["data"] == {"quiet": 2, "closed": 1}
    swept.assert_awaited_once()


def test_watcher_is_declared_with_the_fault_sweeper_role() -> None:
    from kaianolevine_api import identity_registry

    watcher = identity_registry.declared("watcher-cog")
    assert watcher is not None
    assert "fault-sweeper" in watcher.roles
