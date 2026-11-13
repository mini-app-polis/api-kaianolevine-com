"""Sentry issues as a source of signals.

Sentry's internal integration sends an ``issue`` webhook
(``POST /v1/webhooks/sentry``) when an issue is created, resolved,
unresolved, assigned or ignored. Each issue group is one problem, keyed by
its short id (``COGS-1K``), and opens at most one GitHub Issue in the
repository that owns it, through the signal engine (services/signals.py):

* **created** opens the Issue. A delivery seen twice opens one: the streak
  is already escalated, so nothing more is sent.
* **unresolved** (a regression, or a person reopening it) reopens the Issue
  the last streak opened, with a comment, rather than opening another. With
  no earlier Issue, it opens one, as ``created`` does.
* **resolved** closes the Issue, with a comment.
* **assigned** and **ignored** change nothing.

**The latest event.** The issue webhook carries neither the issue's tags
nor its environment. With ``SENTRY_API_TOKEN`` set, a delivery that would
open or reopen an Issue first reads the issue's latest event from the Sentry
API (:func:`latest_event`) for its ``service`` and ``environment`` tags.
Without the token, they are read only where the payload holds them (an
issue's ``tags``, or its ``metadata``).

**Which repository.** An issue that names a ``service`` (the tag every
service sets at ``init_sentry``) opens in that repository, under
``SIGNAL_ISSUE_OWNER``. One that does not opens in its Sentry project's
repository (``SENTRY_PROJECT_REPOS``); a project shared by several services
has none, so an issue there that names no service is skipped and logged. A
project not listed is ignored. When the event cannot be read, a project with
a repository still opens there; one without (``cogs``) is not guessed at: the
delivery is reported as dropped, and the next one for the issue tries again.

**Which environment.** Only production opens an Issue. An environment that
is not production is ignored; an issue whose environment is unknown (no
token, and none in the payload) is taken to be production.

Deterministic: no model is asked anything.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import httpx
from mini_app_polis.logger import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from . import github_issues, job_queue, signals
from .signals import Rule, Signal

logger = get_logger()

KIND = "sentry-issue"

#: The actions that open, reopen or close an Issue. Others are ignored.
OPENING_ACTIONS = frozenset({"created", "unresolved"})
CLOSING_ACTIONS = frozenset({"resolved"})

PRODUCTION = "production"

#: A Sentry issue escalates on its first report: Sentry has already grouped
#: and deduplicated it.
RULE = Rule()

#: The Sentry API an issue's latest event is read from.
SENTRY_API = "https://sentry.io/api/0"

#: How long reading an issue's latest event may take.
TIMEOUT_SECONDS = 5.0

#: The organization slug in an issue's API url.
_ORG_IN_URL = re.compile(r"/api/0/organizations/([A-Za-z0-9_-]+)/issues/")

#: A service tag that can name a repository.
_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass(frozen=True)
class SentryIssue:
    """What one issue webhook says about its issue."""

    short_id: str
    title: str
    project: str
    #: The numeric id and organization slug the Sentry API knows it by.
    issue_id: str | None = None
    org: str | None = None
    link: str | None = None
    culprit: str | None = None
    level: str | None = None
    first_seen: str | None = None
    service: str | None = None
    environment: str | None = None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _tags(issue: Mapping[str, Any]) -> dict[str, str]:
    """An issue's tags, where the payload holds any: pairs or key/value maps."""
    found: dict[str, str] = {}
    for tag in issue.get("tags") or []:
        if isinstance(tag, list | tuple) and len(tag) == 2:
            key, value = tag
        elif isinstance(tag, Mapping):
            key, value = tag.get("key"), tag.get("value")
        else:
            continue
        if isinstance(key, str) and _text(value):
            found[key] = str(value).strip()
    return found


def read_issue(payload: Mapping[str, Any]) -> SentryIssue | None:
    """The issue an issue webhook is about, or None if it names none."""
    data = payload.get("data")
    issue = data.get("issue") if isinstance(data, Mapping) else None
    if not isinstance(issue, Mapping):
        return None
    project = issue.get("project")
    short_id = _text(issue.get("shortId"))
    slug = _text(project.get("slug")) if isinstance(project, Mapping) else None
    if short_id is None or slug is None:
        return None
    tags = _tags(issue)
    metadata = issue.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    link = _text(issue.get("permalink")) or _text(issue.get("web_url"))
    org = _ORG_IN_URL.search(_text(issue.get("url")) or "")
    issue_id = _text(issue.get("id"))
    return SentryIssue(
        short_id=short_id,
        title=_text(issue.get("title")) or short_id,
        project=slug,
        issue_id=issue_id if issue_id and issue_id.isdigit() else None,
        org=org.group(1) if org else None,
        link=link if link and link.startswith("https://") else None,
        culprit=_text(issue.get("culprit")),
        level=_text(issue.get("level")),
        first_seen=_text(issue.get("firstSeen")),
        service=tags.get("service") or _text(metadata.get("service")),
        environment=tags.get("environment") or _text(issue.get("environment")),
    )


class SentryLookupError(Exception):
    """The issue's latest event could not be read."""


async def latest_event(issue: SentryIssue, *, token: str) -> SentryIssue:
    """The issue, with the service and environment of its latest event.

    A tag the event does not carry keeps what the payload said. Raises
    :class:`SentryLookupError` when the event cannot be read.
    """
    if issue.org is None or issue.issue_id is None:
        raise SentryLookupError(f"{issue.short_id} names no organization or issue id")
    path = f"/organizations/{issue.org}/issues/{issue.issue_id}/events/latest/"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{SENTRY_API}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise SentryLookupError(f"GET {path}: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise SentryLookupError(f"GET {path}: Sentry answered {response.status_code}")
    try:
        event = response.json()
    except ValueError as exc:
        raise SentryLookupError(f"GET {path}: not JSON") from exc
    if not isinstance(event, Mapping):
        raise SentryLookupError(f"GET {path}: not an event")
    tags = _tags(event)
    return replace(
        issue,
        service=tags.get("service") or issue.service,
        environment=(
            tags.get("environment")
            or _text(event.get("environment"))
            or issue.environment
        ),
    )


async def report_lookup_dropped(
    issue: SentryIssue, exc: Exception, *, settings: Settings
) -> None:
    """Report an issue whose Issue was not opened for want of its event."""
    await job_queue.report_dropped(
        f"could not read the latest event of Sentry {issue.short_id} "
        f"({issue.project}), so no Issue was opened: {exc}",
        label="signals",
        heading="Signal not escalated",
        settings=settings,
    )


def is_production(issue: SentryIssue) -> bool:
    """Whether the issue is production's. One that names none is taken to be."""
    return issue.environment is None or issue.environment == PRODUCTION


def is_watched(issue: SentryIssue, settings: Settings) -> bool:
    """Whether the issue's Sentry project is one whose issues are signals."""
    return issue.project in settings.SENTRY_PROJECT_REPOS


def repo_for(issue: SentryIssue, settings: Settings) -> str | None:
    """The repository, by name, the issue's GitHub Issue opens in, or None."""
    if issue.service and _REPO_NAME.match(issue.service):
        return issue.service
    return settings.SENTRY_PROJECT_REPOS.get(issue.project) or None


def signal_for(issue: SentryIssue, *, repo: str | None, state: str) -> Signal:
    """The signal one issue webhook carries."""
    title = f"Sentry {issue.short_id}: {issue.title}"
    if state == "recovered":
        return Signal(
            kind=KIND,
            key=issue.short_id,
            state="recovered",
            title=title,
            where=repo,
            link=issue.link,
            details="Resolved in Sentry.",
        )
    named = f" (service {issue.service})" if issue.service else ""
    return Signal(
        kind=KIND,
        key=issue.short_id,
        state="failing",
        title=title,
        where=repo,
        link=issue.link,
        details=(
            f"Sentry reported issue {issue.short_id} in its {issue.project} "
            f"project{named}: {issue.title}"
        ),
        done_when=(
            f"{issue.short_id} is resolved in Sentry. This Issue then closes "
            "itself, with a comment."
        ),
        constraints=(
            "Fix the cause. Do not catch the error only to keep it out of Sentry."
        ),
        sections=(
            ("Sentry issue", issue.short_id),
            ("Project", issue.project),
            ("Service", issue.service or ""),
            ("Culprit", issue.culprit or ""),
            ("Level", issue.level or ""),
        ),
        evidence=(("First seen", issue.first_seen or ""),),
    )


def reopen_comment(issue: SentryIssue) -> str:
    """The comment on an Issue its Sentry issue reopened."""
    lines = [f"Unresolved again in Sentry: {issue.short_id}."]
    if issue.link:
        lines.append(issue.link)
    return "\n".join(lines)


async def _reopen(
    session: AsyncSession, signal: Signal, issue: SentryIssue, *, settings: Settings
) -> str | None:
    """Reopen the last closed streak that opened an Issue, and its Issue.

    None when there is none to reopen, or another delivery reopened it first.
    """
    row = (
        await session.execute(
            text(
                """
                UPDATE signal_streaks
                SET closed_at = NULL, last_seen_at = now(),
                    failures = failures + 1
                WHERE id = (
                    SELECT id FROM signal_streaks
                    WHERE kind = :kind AND key = :key
                      AND closed_at IS NOT NULL AND issue_number IS NOT NULL
                    ORDER BY opened_at DESC LIMIT 1
                  )
                  AND closed_at IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM signal_streaks
                    WHERE kind = :kind AND key = :key AND closed_at IS NULL
                  )
                RETURNING issue_repo, issue_number
                """
            ),
            {"kind": signal.kind, "key": signal.key},
        )
    ).first()
    await session.commit()
    if row is None:
        return None
    repo, number = row
    try:
        await github_issues.reopen(
            repo, number, body=reopen_comment(issue), settings=settings
        )
    except github_issues.IssueError as exc:
        await job_queue.report_dropped(
            f"could not reopen {repo}#{number} for {signal.key}: {exc}",
            label="signals",
            heading="Signal not escalated",
            settings=settings,
        )
        return "dropped"
    logger.info("signals: %s %s reopened %s#%d", signal.kind, signal.key, repo, number)
    return "reopened"


async def observe(
    session: AsyncSession,
    issue: SentryIssue,
    *,
    action: str,
    repo: str | None,
    settings: Settings,
) -> str:
    """Open, reopen or close the issue's GitHub Issue, as ``action`` says."""
    if action in CLOSING_ACTIONS:
        signal = signal_for(issue, repo=repo, state="recovered")
        return await signals.observe(session, signal, rule=RULE, settings=settings)

    signal = signal_for(issue, repo=repo, state="failing")
    open_streak = (
        await session.execute(
            text(
                """
                SELECT escalated_at FROM signal_streaks
                WHERE kind = :kind AND key = :key AND closed_at IS NULL
                """
            ),
            {"kind": signal.kind, "key": signal.key},
        )
    ).first()
    if open_streak is not None and open_streak[0] is not None:
        # Already reported: a redelivery, or created and unresolved both seen.
        return "duplicate"
    if open_streak is None:
        reopened = await _reopen(session, signal, issue, settings=settings)
        if reopened is not None:
            return reopened
    # A first report, or a retry of one whose Issue could not be opened.
    return await signals.observe(session, signal, rule=RULE, settings=settings)
