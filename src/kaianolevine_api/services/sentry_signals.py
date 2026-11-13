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

**Which repository.** An issue that names a ``service`` (the tag every
service sets at ``init_sentry``) opens in that repository, under
``SIGNAL_ISSUE_OWNER``. One that does not opens in its Sentry project's
repository (``SENTRY_PROJECT_REPOS``); a project shared by several services
has none, so an issue there that names no service is skipped and logged. A
project not listed is ignored. The issue webhook does not carry the issue's
tags, so the service is read only where the payload holds it (an issue's
``tags``, or its ``metadata``).

**Which environment.** Only production opens an Issue. The issue webhook
does not carry the issue's environment either: an environment read from the
payload (its ``tags``, or the issue itself) that is not production is
ignored, and an issue that names none is taken to be production.

Deterministic: no model is asked anything.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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

#: A service tag that can name a repository.
_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass(frozen=True)
class SentryIssue:
    """What one issue webhook says about its issue."""

    short_id: str
    title: str
    project: str
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
    return SentryIssue(
        short_id=short_id,
        title=_text(issue.get("title")) or short_id,
        project=slug,
        link=link if link and link.startswith("https://") else None,
        culprit=_text(issue.get("culprit")),
        level=_text(issue.get("level")),
        first_seen=_text(issue.get("firstSeen")),
        service=tags.get("service") or _text(metadata.get("service")),
        environment=tags.get("environment") or _text(issue.get("environment")),
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
