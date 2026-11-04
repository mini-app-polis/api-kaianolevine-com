"""Conformance findings as signals: an Issue per rule per repo and source,
closed only by a complete, clean evaluation.

Findings go through the real ``POST /v1/evaluations`` route and the real
``signal_streaks`` table; GitHub is stubbed at the Issue writer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.services import conformance_signals, github_issues, signals

REPO = "api-kaianolevine-com"
DET = "conformance_deterministic"
LLM = "conformance_llm"


def _opened(repo: str, **_: object) -> github_issues.Issue:
    return github_issues.Issue(repo, 7, f"https://github.com/{repo}/issues/7")


@pytest.fixture
def issues():
    with (
        patch.object(
            github_issues, "open_issue", AsyncMock(side_effect=_opened)
        ) as opened,
        patch.object(github_issues, "comment", AsyncMock()) as commented,
        patch.object(github_issues, "close", AsyncMock()) as closed,
    ):
        yield SimpleNamespace(open=opened, comment=commented, close=closed)


async def _post(
    client: AsyncClient,
    *,
    severity: str,
    violation_id: str | None = "DOC-004",
    source: str = DET,
    run_id: str = "deterministic-6.0-aaaaaaaaaaaa",
    finding: str = "SIGNALS_APP_CLIENT_ID is read but not in .env.example.",
    repo: str = REPO,
) -> None:
    body = {
        "repo": repo,
        "dimension": "structural_conformance",
        "severity": severity,
        "run_id": run_id,
        "finding": finding,
        "suggestion": "List it in .env.example.",
        "standards_version": "6.0",
        "source": source,
    }
    if violation_id is not None:
        body["violation_id"] = violation_id
    resp = await client.post("/v1/evaluations", json=body)
    assert resp.status_code == 200, resp.text


async def _streaks(session: AsyncSession) -> list[tuple]:
    rows = await session.execute(
        text(
            "SELECT kind, key, failures, closed_at IS NOT NULL "
            "FROM signal_streaks ORDER BY opened_at, key"
        )
    )
    return [tuple(r) for r in rows]


@pytest.mark.asyncio
async def test_a_warn_finding_opens_an_issue_in_its_repo(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(client, severity="WARN")

    issues.open.assert_awaited_once()
    assert issues.open.await_args.args == (f"mini-app-polis/{REPO}",)
    opened = issues.open.await_args.kwargs
    assert opened["marker"] == f"conformance/{REPO}/{DET}/DOC-004"
    assert opened["title"] == f"{REPO}: DOC-004 (deterministic conformance)"
    assert "SIGNALS_APP_CLIENT_ID" in opened["body"]
    assert await _streaks(db_session) == [
        ("conformance", f"{REPO}/{DET}/DOC-004", 1, False)
    ]


@pytest.mark.asyncio
async def test_the_same_finding_in_the_next_run_comments_on_the_issue(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(client, severity="WARN", run_id="deterministic-6.0-aaaaaaaaaaaa")
    await _post(client, severity="WARN", run_id="deterministic-6.0-bbbbbbbbbbbb")

    issues.open.assert_awaited_once()
    issues.comment.assert_awaited_once()
    assert await _streaks(db_session) == [
        ("conformance", f"{REPO}/{DET}/DOC-004", 2, False)
    ]


@pytest.mark.asyncio
async def test_a_redelivered_finding_is_not_observed_twice(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(client, severity="WARN")
    await _post(client, severity="WARN")

    issues.comment.assert_not_awaited()
    assert await _streaks(db_session) == [
        ("conformance", f"{REPO}/{DET}/DOC-004", 1, False)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("severity", ["INFO", "SUCCESS"])
async def test_deferred_and_downgraded_findings_open_nothing(
    client: AsyncClient, db_session: AsyncSession, issues, severity: str
) -> None:
    await _post(client, severity=severity)

    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == []


@pytest.mark.asyncio
async def test_a_finding_from_another_source_opens_nothing(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(client, severity="ERROR", source="flow_inline")

    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == []


@pytest.mark.asyncio
async def test_a_clean_evaluation_closes_every_issue_for_its_repo_and_source(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(client, severity="WARN", violation_id="DOC-004")
    await _post(client, severity="ERROR", violation_id="API-006")
    await _post(client, severity="WARN", violation_id="FE-005", source=LLM)
    await _post(
        client,
        severity="SUCCESS",
        violation_id=None,
        run_id="deterministic-6.0-cccccccccccc",
        finding=f"{REPO} passed all deterministic checks for standards v6.0.",
    )

    assert issues.close.await_count == 2
    closing = issues.close.await_args.kwargs["body"]
    assert "deterministic conformance evaluation came back clean" in closing
    assert await _streaks(db_session) == [
        ("conformance", f"{REPO}/{DET}/DOC-004", 1, True),
        ("conformance", f"{REPO}/{DET}/API-006", 1, True),
        ("conformance", f"{REPO}/{LLM}/FE-005", 1, False),
    ]


@pytest.mark.asyncio
async def test_a_run_that_still_reports_other_findings_closes_nothing(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(client, severity="WARN", violation_id="DOC-004")
    await _post(
        client,
        severity="WARN",
        violation_id="API-006",
        run_id="deterministic-6.0-bbbbbbbbbbbb",
    )

    issues.close.assert_not_awaited()
    assert [closed for *_, closed in await _streaks(db_session)] == [False, False]


@pytest.mark.asyncio
async def test_a_clean_evaluation_of_another_repo_closes_nothing_here(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(client, severity="WARN")
    await _post(client, severity="SUCCESS", violation_id=None, repo=f"{REPO}-x")

    issues.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_repo_not_evaluated_opens_an_issue_in_evaluator_cog(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(
        client,
        severity="ERROR",
        violation_id="STATUS",
        finding=(
            f"{REPO} was declared active but was not evaluated in this run: "
            "clone failed. Its conformance is unknown, not clean."
        ),
    )

    issues.open.assert_awaited_once()
    assert issues.open.await_args.args == ("mini-app-polis/evaluator-cog",)
    assert (
        issues.open.await_args.kwargs["marker"]
        == f"evaluator-status/{REPO}/{DET}/not-evaluated"
    )

    await _post(
        client,
        severity="SUCCESS",
        violation_id=None,
        run_id="deterministic-6.0-dddddddddddd",
    )
    issues.close.assert_awaited_once()
    assert issues.close.await_args.args == ("mini-app-polis/evaluator-cog", 7)


@pytest.mark.asyncio
async def test_an_llm_check_not_run_is_its_own_status_kind(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    await _post(
        client,
        severity="WARN",
        violation_id="STATUS",
        source=LLM,
        finding=(
            f"{REPO} was not assessed against the LLM checks for standards "
            "v6.0: ANTHROPIC_API_KEY is not set."
        ),
    )

    assert (
        issues.open.await_args.kwargs["marker"]
        == f"evaluator-status/{REPO}/{LLM}/not-assessed"
    )


@pytest.mark.asyncio
async def test_outside_production_a_finding_observes_nothing(
    client: AsyncClient, db_session: AsyncSession, issues, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    await _post(client, severity="WARN")

    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == []


@pytest.mark.asyncio
async def test_a_signal_error_never_fails_the_write(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    with patch.object(
        conformance_signals, "observe_row", AsyncMock(side_effect=RuntimeError("db"))
    ):
        await _post(client, severity="WARN")

    rows = await db_session.execute(text("SELECT count(*) FROM pipeline_evaluations"))
    assert rows.scalar_one() == 1


def test_a_finding_signal_carries_what_ends_it() -> None:
    signal = conformance_signals.failing_signal(
        repo=REPO,
        source=LLM,
        severity="WARN",
        violation_id="FE-005",
        finding="A form does not call useForm.",
        suggestion="Use react-hook-form.",
    )
    assert signal is not None
    assert signal.where == REPO
    assert signal.done_when == (
        f"A complete LLM conformance evaluation of {REPO} comes back clean."
    )
    assert ("Suggestion", "Use react-hook-form.") in signal.sections
    assert isinstance(signal, signals.Signal)
