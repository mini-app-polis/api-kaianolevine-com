"""POST /v1/webhooks/sentry: one GitHub Issue per production Sentry issue.

The route and the engine run against the real ``signal_streaks`` table;
GitHub is stubbed at the Issue writer (``github_issues``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.services import github_issues, job_queue

pytestmark = pytest.mark.asyncio

SECRET = "test-sentry-secret"
LINK = "https://kaianolevine.sentry.io/issues/1170820242/"


def _issue_payload(
    action: str = "created",
    *,
    short_id: str = "COGS-1K",
    project: str = "cogs",
    tags: list | None = None,
) -> dict:
    issue: dict = {
        "id": "1170820242",
        "shortId": short_id,
        "title": "ValueError: bad tag",
        "culprit": "deejay_cog.tagging in retag",
        "level": "error",
        "firstSeen": "2026-11-12T10:00:00Z",
        "permalink": LINK,
        "project": {"id": "1", "slug": project, "name": project},
    }
    if tags is not None:
        issue["tags"] = tags
    return {"action": action, "data": {"issue": issue}}


async def _post(
    client: AsyncClient,
    payload: dict,
    *,
    resource: str = "issue",
    secret: str = SECRET,
):
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return await client.post(
        "/v1/webhooks/sentry",
        content=body,
        headers={
            "Sentry-Hook-Resource": resource,
            "Sentry-Hook-Signature": digest,
            "Content-Type": "application/json",
        },
    )


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
        patch.object(github_issues, "reopen", AsyncMock()) as reopened,
    ):
        yield SimpleNamespace(
            open=opened, comment=commented, close=closed, reopen=reopened
        )


def _result(resp) -> dict:
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _streaks(session: AsyncSession) -> list[tuple]:
    rows = await session.execute(
        text(
            "SELECT kind, key, issue_repo, issue_number, closed_at IS NOT NULL "
            "FROM signal_streaks ORDER BY opened_at"
        )
    )
    return [tuple(r) for r in rows]


async def test_a_new_issue_opens_one_github_issue_in_its_services_repo(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    payload = _issue_payload(tags=[["service", "deejay-cog"]])
    first = _result(await _post(client, payload))
    again = _result(await _post(client, payload))

    assert first["outcome"] == "escalated"
    assert again["outcome"] == "duplicate"
    issues.open.assert_awaited_once()
    assert issues.open.await_args.args == ("mini-app-polis/deejay-cog",)
    kwargs = issues.open.await_args.kwargs
    assert kwargs["marker"] == "sentry-issue/COGS-1K"
    assert kwargs["title"] == "Sentry COGS-1K: ValueError: bad tag"
    assert LINK in kwargs["body"]
    issues.comment.assert_not_awaited()
    assert await _streaks(db_session) == [
        ("sentry-issue", "COGS-1K", "mini-app-polis/deejay-cog", 7, False)
    ]


async def test_a_project_with_a_repository_needs_no_service(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    data = _result(
        await _post(
            client, _issue_payload(short_id="PYTHON-APIS-3", project="python-apis")
        )
    )

    assert data["outcome"] == "escalated"
    assert issues.open.await_args.args == ("mini-app-polis/api-kaianolevine-com",)


async def test_a_cogs_issue_naming_no_service_is_skipped(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    data = _result(await _post(client, _issue_payload()))

    assert data == {"outcome": None, "reason": "no_repository"}
    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == []


async def test_resolving_closes_and_unresolving_reopens_the_same_issue(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    tags = [["service", "deejay-cog"]]
    await _post(client, _issue_payload(tags=tags))

    resolved = _result(await _post(client, _issue_payload("resolved", tags=tags)))
    assert resolved["outcome"] == "recovered"
    issues.close.assert_awaited_once()
    assert issues.close.await_args.args == ("mini-app-polis/deejay-cog", 7)

    unresolved = _result(await _post(client, _issue_payload("unresolved", tags=tags)))
    assert unresolved["outcome"] == "reopened"
    issues.reopen.assert_awaited_once()
    assert issues.reopen.await_args.args == ("mini-app-polis/deejay-cog", 7)
    issues.open.assert_awaited_once()
    assert await _streaks(db_session) == [
        ("sentry-issue", "COGS-1K", "mini-app-polis/deejay-cog", 7, False)
    ]

    again = _result(await _post(client, _issue_payload("unresolved", tags=tags)))
    assert again["outcome"] == "duplicate"
    issues.reopen.assert_awaited_once()


async def test_an_issue_that_could_not_open_is_retried_by_the_next_delivery(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    issues.open.side_effect = github_issues.IssueError("down")
    tags = [["service", "deejay-cog"]]
    with patch.object(job_queue, "report_dropped", AsyncMock()) as reported:
        first = _result(await _post(client, _issue_payload(tags=tags)))
    assert first["outcome"] == "dropped"
    reported.assert_awaited_once()

    issues.open.side_effect = _opened
    second = _result(await _post(client, _issue_payload("unresolved", tags=tags)))
    assert second["outcome"] == "escalated"


async def test_a_development_issue_opens_nothing(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    payload = _issue_payload(
        tags=[["service", "deejay-cog"], ["environment", "development"]]
    )
    data = _result(await _post(client, payload))

    assert data == {"outcome": None, "reason": "not_production"}
    issues.open.assert_not_awaited()


async def test_outside_production_the_api_observes_nothing(
    client: AsyncClient, db_session: AsyncSession, issues, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    data = _result(
        await _post(client, _issue_payload(tags=[["service", "deejay-cog"]]))
    )

    assert data["reason"] == "observing_off"
    issues.open.assert_not_awaited()
    assert await _streaks(db_session) == []


@pytest.mark.parametrize(
    ("resource", "payload", "reason"),
    [
        ("installation", _issue_payload(), "other_resource"),
        ("issue", _issue_payload("assigned"), "other_action"),
        ("issue", _issue_payload("ignored"), "other_action"),
        ("issue", {"action": "created", "data": {}}, "no_issue"),
        ("issue", _issue_payload(project="elsewhere"), "other_project"),
    ],
)
async def test_what_is_not_a_watched_issue_answers_200_and_does_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    issues,
    resource: str,
    payload: dict,
    reason: str,
) -> None:
    data = _result(await _post(client, payload, resource=resource))

    assert data == {"outcome": None, "reason": reason}
    issues.open.assert_not_awaited()


async def test_a_bad_signature_is_refused(
    client: AsyncClient, db_session: AsyncSession, issues
) -> None:
    resp = await _post(client, _issue_payload(), secret="wrong")

    assert resp.status_code == 401
    issues.open.assert_not_awaited()


async def test_without_a_secret_every_delivery_is_refused(
    client: AsyncClient, db_session: AsyncSession, issues, monkeypatch
) -> None:
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "")
    resp = await _post(client, _issue_payload())

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "config_error"
    issues.open.assert_not_awaited()
