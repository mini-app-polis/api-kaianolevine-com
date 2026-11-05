"""Detected problems, held as streaks, escalated to Issues only when they stick.

The fleet sees its own failures: a CI run that failed, a cog run that
crashed, a burst of 5xx. Most heal on their own, so an Issue for each would
bury the repositories. This module is the one place that decides which are
reported. None reaches the board: a person files a task from an Issue by hand.

**A detector** turns an event this service already handles into a
:class:`Signal`: its ``kind``, a stable ``key`` naming the problem, and
whether this event says it is ``failing`` or has ``recovered``. Detectors are
small and per source (``ci_signals`` is the first); nothing below knows what a
workflow run or a finding is.

**A streak** is one problem from its first failure until it recovers: one
open row per (kind, key) in ``signal_streaks``. A failure opens one or bumps
the open one; a recovery closes it, so the next failure starts afresh.

**A rule** per kind says when a streak escalates. A kind with no recovery
event of its own (a dependency's 5xx has no "and now it works") may close by
a quiet window instead: :func:`close_if_quiet` closes a streak that has gone
the window without a failure, called by a scheduled sweep
(``fault_signals.sweep_quiet``) and again when the next failure arrives, so
a sweep that did not run delays a close and never loses one. A rule may
instead leave its Issue to a person or a fixing pull request
(``reopen_closed_issue``): the streak never closes on its own, and its next
failure asks GitHub whether the Issue is still open. A closed one means the
problem was thought fixed, so the failure starts a new streak whose Issue
links the old one. A kind may have a
remediation, tried before anything is reported: for CI, re-running the
failed jobs once. A remediation that cannot start escalates at once rather
than leaving the streak to wait on a failure that may never come. A streak
that recovers before escalating closes quietly and is only logged.

**Escalating** opens one GitHub Issue in the repository the problem is about
(services.github_issues), once per streak: the claim on the streak is atomic,
so a delivery seen twice cannot open twice, and the writer keeps one open
Issue per kind and key. An Issue that cannot be opened is reported, and the
claim released for the next failure to retry. Each further failure of an
escalated streak comments on its Issue.

**Recovering** an escalated streak comments on its Issue and closes it. A
streak that closes by its kind's recovery event names that event and links
it; one that closes by its quiet window says the window passed without a
failure. A streak that never escalated has no Issue, and sends nothing. A
close that does not land is reported, and the recovery stands: the Issue
stays open for a person.

Streaks escalated before Issues were board tasks. Their recovery still asks
task-manager-cog, through its queue, to comment on the task and move it to
Review, where a person confirms the fix and closes it.

**The Issue says what ends it.** A detector knows where the problem is, the
outcome that ends it (the check passing again), and what a fix must not do
(silence it), so the Issue says so, and a task filed from it needs nothing
the detector already knew.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from mini_app_polis.api.contract import TaskUpdateRequest
from mini_app_polis.logger import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from . import github_issues, job_queue, task_dispatch

logger = get_logger()

#: What a detector says one event means.
State = Literal["failing", "recovered"]

#: What :func:`observe` did with a signal. For logs and tests.
Outcome = Literal[
    "opened",  # a first failure, waiting under its kind's rule
    "bumped",  # a further failure of a streak already escalated or remediating
    "remediating",  # a remediation was started instead of an Issue
    "escalated",  # an Issue was opened
    "dropped",  # the Issue could not be opened; the next failure retries
    "recovered",  # an escalated streak closed
    "healed",  # a streak closed before it escalated
    "quiet",  # a recovery with no open streak
]

# GitHub's and the board's limits; detector text is cut to fit, not refused.
_TITLE_MAX = 250
_TEXT_MAX = 20_000

#: The column a recovered streak's task moves to, for a person to confirm.
REVIEW_SECTION = "Review"


@dataclass(frozen=True)
class Signal:
    """One event, as a detector reads it."""

    kind: str
    key: str
    state: State
    title: str
    #: The repository the problem is about, by name; its Issue opens there.
    where: str | None = None
    link: str | None = None
    details: str | None = None
    #: The observable outcome that ends it.
    done_when: str | None = None
    #: What a fix must not do.
    constraints: str | None = None
    sections: tuple[tuple[str, str], ...] = ()
    #: Whether this event may be remediated (a re-run is pointless on a run
    #: that is itself a re-run).
    remediable: bool = False
    #: What a remediation needs to act on this event (a repo, a run id).
    context: dict[str, Any] = field(default_factory=dict)
    #: This one event's particulars (when, which request), as labelled lines:
    #: in the Issue it opens, and in the comment it posts on one already open.
    evidence: tuple[tuple[str, str], ...] = ()
    #: Posted on an Issue as soon as it opens, with ``{number}`` replaced by
    #: its number: what only the number lets the Issue say.
    opened_comment: str | None = None


#: Starts a remediation for a signal. Returns whether it started.
Remediation = Callable[[Signal, Settings], Awaitable[bool]]


@dataclass(frozen=True)
class Rule:
    """When a kind's streak becomes an Issue."""

    #: Failures in one streak before it escalates.
    escalate_after: int = 1
    #: Tried before escalating; at most ``max_remediations`` per streak.
    remediation: Remediation | None = None
    max_remediations: int = 0
    #: For a kind with no recovery event: a streak with no failure for this
    #: long is over (:func:`close_if_quiet`), and the next failure starts a
    #: new one.
    quiet_window: dt.timedelta | None = None
    #: An escalated streak whose Issue was closed (by a person, or a pull
    #: request that fixes it) is over: its next failure starts a new streak,
    #: whose Issue links the closed one. Costs that failure one read of the
    #: Issue from GitHub.
    reopen_closed_issue: bool = False


def _cut(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def issue_repo_for(signal: Signal, settings: Settings) -> str | None:
    """The repository, ``owner/name``, a signal's Issue opens in, or None.

    A detector that knows the full name puts it in ``context`` (CI does);
    one that names the repository alone is in ``SIGNAL_ISSUE_OWNER``.
    """
    full = signal.context.get("repo")
    if isinstance(full, str) and "/" in full:
        return full
    if signal.where:
        return f"{settings.SIGNAL_ISSUE_OWNER}/{signal.where}"
    return None


def issue_marker(signal: Signal) -> str:
    """What names a streak's Issue: one open Issue per kind and key."""
    return f"{signal.kind}/{signal.key}"


def _https(link: str | None) -> str | None:
    return link if link and link.startswith("https://") else None


def _evidence_lines(signal: Signal) -> list[str]:
    return [f"**{label}:** {value}" for label, value in signal.evidence if value]


def issue_body(
    signal: Signal,
    *,
    failures: int,
    opened_at: dt.datetime,
    previous: int | None = None,
) -> str:
    """The body of the Issue an escalated streak opens.

    ``previous`` is the number of the closed Issue, in the same repository,
    of the streak this one follows.
    """
    lines: list[str] = []
    if signal.details:
        lines += [signal.details, ""]
    if signal.done_when:
        lines.append(f"**Done when:** {signal.done_when}")
    if signal.constraints:
        lines.append(f"**Constraints:** {signal.constraints}")
    lines += [f"**{label}:** {text_}" for label, text_ in signal.sections if text_]
    lines.append(
        f"**Streak:** {failures} failure(s) since {opened_at:%Y-%m-%d %H:%M} UTC"
    )
    if previous is not None:
        lines.append(f"**Recurs:** #{previous} was closed, and it failed again.")
    lines += _evidence_lines(signal)
    link = _https(signal.link)
    if link:
        lines.append(f"**Latest failure:** {link}")
    return _cut("\n".join(lines), _TEXT_MAX)


def failure_comment(signal: Signal, *, failures: int) -> str:
    """The comment a further failure of an escalated streak posts."""
    lines = [f"Failed again: {failures} failure(s) in this streak."]
    lines += _evidence_lines(signal)
    if signal.details:
        lines.append(signal.details)
    link = _https(signal.link)
    if link:
        lines.append(link)
    return _cut("\n".join(lines), _TEXT_MAX)


def _recovered_lines(signal: Signal) -> list[str]:
    lines = [f"Recovered: {signal.title}."]
    if signal.details:
        lines.append(signal.details)
    link = _https(signal.link)
    if link:
        lines.append(link)
    return lines


def recovery_comment(signal: Signal) -> str:
    """The comment for a task whose streak the event ``signal`` closed."""
    lines = _recovered_lines(signal)
    lines.append("Moved to Review for a person to confirm the fix and close the task.")
    return _cut("\n".join(lines), _TEXT_MAX)


def issue_recovery_comment(signal: Signal) -> str:
    """The comment that closes an Issue whose streak ``signal`` closed."""
    return _cut("\n".join(_recovered_lines(signal)), _TEXT_MAX)


def _span(window: dt.timedelta) -> str:
    """A window in whole hours where it is whole hours, else in minutes."""
    minutes = int(window.total_seconds() // 60)
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minutes"


def _quiet_line(signal: Signal, window: dt.timedelta) -> str:
    return f"Recovered: {_span(window)} passed without another {signal.key}."


def quiet_comment(signal: Signal, window: dt.timedelta) -> str:
    """The comment for a task whose streak closed by its quiet window."""
    return _cut(
        f"{_quiet_line(signal, window)}\n"
        "Moved to Review for a person to confirm the fix and close the task.",
        _TEXT_MAX,
    )


def issue_quiet_comment(signal: Signal, window: dt.timedelta) -> str:
    """The comment that closes an Issue whose streak went quiet."""
    return _quiet_line(signal, window)


async def _request_review(
    signal: Signal, *, external_id: str, comment: str, settings: Settings
) -> bool:
    """Ask for the task to be commented on and moved to Review. Never raises.

    Returns whether the request landed; one that did not is reported.
    """
    request = TaskUpdateRequest(
        external_id=external_id, comment=comment, section=REVIEW_SECTION
    )
    try:
        await task_dispatch.dispatch_task_update(request, settings=settings)
    except task_dispatch.DispatchError as exc:
        await job_queue.report_dropped(
            exc.detail or f"could not enqueue the update for {external_id}",
            label="signals",
            heading="Recovery not sent",
            settings=settings,
        )
        return False
    logger.info(
        "signals: %s %s sent %s to Review", signal.kind, signal.key, external_id
    )
    return True


async def _close_issue(
    signal: Signal, repo: str, number: int, *, comment: str, settings: Settings
) -> bool:
    """Comment on the streak's Issue and close it. Never raises.

    Returns whether it closed; one that did not is reported, and stays open
    for a person.
    """
    try:
        await github_issues.close(repo, number, body=comment, settings=settings)
    except github_issues.IssueError as exc:
        await job_queue.report_dropped(
            f"could not close {repo}#{number}: {exc}",
            label="signals",
            heading="Recovery not sent",
            settings=settings,
        )
        return False
    logger.info("signals: %s %s closed %s#%d", signal.kind, signal.key, repo, number)
    return True


async def _comment_failure(
    signal: Signal, repo: str, number: int, *, failures: int, settings: Settings
) -> None:
    """Tell the streak's Issue it failed again. Never raises; only logged."""
    try:
        await github_issues.comment(
            repo,
            number,
            failure_comment(signal, failures=failures),
            settings=settings,
        )
    except github_issues.IssueError as exc:
        logger.warning("signals: no comment on %s#%d: %s", repo, number, exc)


async def _closed(
    signal: Signal,
    *,
    task_external_id: str | None,
    issue_repo: str | None,
    issue_number: int | None,
    issue_comment: str,
    task_comment: str,
    settings: Settings,
) -> None:
    """Tell what an escalated streak made, an Issue or an older task, it closed."""
    if issue_repo and issue_number is not None:
        await _close_issue(
            signal, issue_repo, issue_number, comment=issue_comment, settings=settings
        )
    elif task_external_id:
        await _request_review(
            signal,
            external_id=task_external_id,
            comment=task_comment,
            settings=settings,
        )


async def _recover(
    session: AsyncSession, signal: Signal, *, settings: Settings
) -> Outcome:
    row = (
        await session.execute(
            text(
                """
                UPDATE signal_streaks SET closed_at = now()
                WHERE kind = :kind AND key = :key AND closed_at IS NULL
                RETURNING escalated_at, failures, task_external_id,
                          issue_repo, issue_number
                """
            ),
            {"kind": signal.kind, "key": signal.key},
        )
    ).first()
    await session.commit()
    if row is None:
        return "quiet"
    escalated_at, failures, task_external_id, issue_repo, issue_number = row
    if escalated_at is None:
        logger.info(
            "signals: %s %s healed after %d failure(s); nothing was reported",
            signal.kind,
            signal.key,
            failures,
        )
        return "healed"
    logger.info("signals: %s %s recovered", signal.kind, signal.key)
    await _closed(
        signal,
        task_external_id=task_external_id,
        issue_repo=issue_repo,
        issue_number=issue_number,
        issue_comment=issue_recovery_comment(signal),
        task_comment=recovery_comment(signal),
        settings=settings,
    )
    return "recovered"


async def close_if_quiet(
    session: AsyncSession,
    signal: Signal,
    window: dt.timedelta,
    *,
    settings: Settings,
) -> bool:
    """Close the open streak if it has gone ``window`` without a failure.

    Closed as of the end of its window, not now: that is when it was over.
    An escalated streak's Issue is commented and closed. Returns whether a
    streak closed.
    """
    row = (
        await session.execute(
            text(
                """
                UPDATE signal_streaks
                SET closed_at = last_seen_at + make_interval(secs => :secs)
                WHERE kind = :kind AND key = :key AND closed_at IS NULL
                  AND last_seen_at < now() - make_interval(secs => :secs)
                RETURNING escalated_at, failures, task_external_id,
                          issue_repo, issue_number
                """
            ),
            {
                "kind": signal.kind,
                "key": signal.key,
                "secs": window.total_seconds(),
            },
        )
    ).first()
    await session.commit()
    if row is None:
        return False
    escalated_at, failures, task_external_id, issue_repo, issue_number = row
    logger.info(
        "signals: %s %s went quiet after %d failure(s)%s",
        signal.kind,
        signal.key,
        failures,
        "" if escalated_at is not None else "; nothing was reported",
    )
    if escalated_at is not None:
        await _closed(
            signal,
            task_external_id=task_external_id,
            issue_repo=issue_repo,
            issue_number=issue_number,
            issue_comment=issue_quiet_comment(signal, window),
            task_comment=quiet_comment(signal, window),
            settings=settings,
        )
    return True


async def _issue_closed(repo: str, number: int, *, settings: Settings) -> bool:
    """Whether the Issue is closed. One GitHub can't answer counts as open."""
    try:
        return not await github_issues.is_open(repo, number, settings=settings)
    except github_issues.IssueError as exc:
        logger.warning("signals: could not read %s#%d: %s", repo, number, exc)
        return False


async def _post_opened_comment(
    signal: Signal, issue: github_issues.Issue, *, settings: Settings
) -> None:
    """Post the signal's opening comment on its new Issue. Only logged."""
    if not signal.opened_comment:
        return
    try:
        await github_issues.comment(
            issue.repo,
            issue.number,
            _cut(signal.opened_comment.format(number=issue.number), _TEXT_MAX),
            settings=settings,
        )
    except github_issues.IssueError as exc:
        logger.warning(
            "signals: no opening comment on %s#%d: %s", issue.repo, issue.number, exc
        )


async def _escalate(
    session: AsyncSession,
    signal: Signal,
    *,
    streak_id: Any,
    failures: int,
    opened_at: dt.datetime,
    settings: Settings,
    previous: int | None = None,
) -> Outcome:
    claimed = (
        await session.execute(
            text(
                """
                UPDATE signal_streaks SET escalated_at = now()
                WHERE id = :id AND escalated_at IS NULL
                RETURNING id
                """
            ),
            {"id": streak_id},
        )
    ).first()
    await session.commit()
    if claimed is None:
        return "bumped"

    repo = issue_repo_for(signal, settings)
    try:
        if repo is None:
            raise github_issues.IssueError("the signal names no repository")
        issue = await github_issues.open_issue(
            repo,
            marker=issue_marker(signal),
            title=_cut(signal.title, _TITLE_MAX),
            body=issue_body(
                signal, failures=failures, opened_at=opened_at, previous=previous
            ),
            settings=settings,
        )
    except github_issues.IssueError as exc:
        # Release the claim, so the next failure of this streak tries again.
        await session.execute(
            text("UPDATE signal_streaks SET escalated_at = NULL WHERE id = :id"),
            {"id": streak_id},
        )
        await session.commit()
        await job_queue.report_dropped(
            f"could not open an Issue for {signal.kind} {signal.key}: {exc}",
            label="signals",
            heading="Signal not escalated",
            settings=settings,
        )
        return "dropped"
    await session.execute(
        text(
            "UPDATE signal_streaks SET issue_repo = :repo, issue_number = :number "
            "WHERE id = :id"
        ),
        {"id": streak_id, "repo": issue.repo, "number": issue.number},
    )
    await session.commit()
    logger.info("signals: %s escalated as %s#%d", signal.key, issue.repo, issue.number)
    await _post_opened_comment(signal, issue, settings=settings)
    return "escalated"


async def _record_failure(session: AsyncSession, signal: Signal) -> Any:
    """Open a streak for this failure, or bump the open one; its row."""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO signal_streaks
                  (kind, key, title, link, details, failures, opened_at, last_seen_at)
                VALUES (:kind, :key, :title, :link, :details, 1, now(), now())
                ON CONFLICT (kind, key) WHERE closed_at IS NULL
                DO UPDATE SET failures = signal_streaks.failures + 1,
                              last_seen_at = now(),
                              title = EXCLUDED.title,
                              link = EXCLUDED.link,
                              details = EXCLUDED.details
                RETURNING id, failures, remediations, escalated_at, opened_at,
                          issue_repo, issue_number
                """
            ),
            {
                "kind": signal.kind,
                "key": signal.key,
                "title": _cut(signal.title, _TITLE_MAX),
                "link": signal.link,
                "details": signal.details,
            },
        )
    ).one()
    await session.commit()
    return row


async def observe(
    session: AsyncSession,
    signal: Signal,
    *,
    rule: Rule,
    settings: Settings,
) -> Outcome:
    """Record one signal under its kind's rule, and act on it."""
    if signal.state == "recovered":
        return await _recover(session, signal, settings=settings)

    if rule.quiet_window is not None:
        await close_if_quiet(session, signal, rule.quiet_window, settings=settings)

    row = await _record_failure(session, signal)
    previous: int | None = None
    if (
        rule.reopen_closed_issue
        and row.escalated_at is not None
        and row.issue_repo
        and row.issue_number is not None
        and await _issue_closed(row.issue_repo, row.issue_number, settings=settings)
    ):
        # Closed as though fixed, and failing again: this failure is the
        # first of a new streak, whose Issue links the old one.
        await session.execute(
            text("UPDATE signal_streaks SET closed_at = now() WHERE id = :id"),
            {"id": row.id},
        )
        await session.commit()
        logger.info(
            "signals: %s %s recurs after %s#%d was closed",
            signal.kind,
            signal.key,
            row.issue_repo,
            row.issue_number,
        )
        previous = row.issue_number
        row = await _record_failure(session, signal)
    (
        streak_id,
        failures,
        remediations,
        escalated_at,
        opened_at,
        issue_repo,
        issue_number,
    ) = row

    if escalated_at is not None:
        if issue_repo and issue_number is not None:
            await _comment_failure(
                signal, issue_repo, issue_number, failures=failures, settings=settings
            )
        return "bumped"

    if (
        rule.remediation is not None
        and signal.remediable
        and remediations < rule.max_remediations
    ):
        claimed = (
            await session.execute(
                text(
                    """
                    UPDATE signal_streaks SET remediations = remediations + 1
                    WHERE id = :id AND remediations = :seen
                    RETURNING id
                    """
                ),
                {"id": streak_id, "seen": remediations},
            )
        ).first()
        await session.commit()
        if claimed is None:
            # Another delivery of the same failure claimed it.
            return "bumped"
        if await rule.remediation(signal, settings):
            logger.info("signals: %s remediating", signal.key)
            return "remediating"
        # Could not start: nothing is coming to retry it, so escalate now.
        return await _escalate(
            session,
            signal,
            streak_id=streak_id,
            failures=failures,
            opened_at=opened_at,
            settings=settings,
            previous=previous,
        )

    if failures < rule.escalate_after:
        return "opened"
    return await _escalate(
        session,
        signal,
        streak_id=streak_id,
        failures=failures,
        opened_at=opened_at,
        settings=settings,
        previous=previous,
    )
