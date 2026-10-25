"""Detected problems, held as streaks, escalated to tasks only when they stick.

The fleet sees its own failures: a CI run that failed, a cog run that
crashed, a conformance finding, a burst of 5xx. Most heal on their own, so a
task for each would bury the board. This module is the one place that decides
which become tasks.

**A detector** turns an event this service already handles into a
:class:`Signal`: its ``kind``, a stable ``key`` naming the problem, and
whether this event says it is ``failing`` or has ``recovered``. Detectors are
small and per source (``ci_signals`` is the first); nothing below knows what a
workflow run or a finding is.

**A streak** is one problem from its first failure until it recovers: one
open row per (kind, key) in ``signal_streaks``. A failure opens one or bumps
the open one; a recovery closes it, so the next failure starts afresh.

**A rule** per kind says when a streak escalates. A kind with no recovery
event of its own (a 5xx has no "and now it works") closes by a quiet window
instead: a failure arriving after the window has passed with none closes the
old streak and opens a new one. Nothing runs on a timer; the window is
applied when the next failure arrives. A kind may have a
remediation, tried before anything reaches the board: for CI, re-running the
failed jobs once. A remediation that cannot start escalates at once rather
than leaving the streak to wait on a failure that may never come. A streak
that recovers before escalating closes quietly and is only logged.

**Escalating** is one task request to task-manager-cog, made once per streak:
the external id is ``<kind>.<key>#<streak id>``, and the claim on the streak
is atomic, so a delivery seen twice cannot ask twice. A request that does not
land is reported, and the claim released for the next failure to retry.

**Recovering** an escalated streak asks task-manager-cog, through the same
queue, to comment on the streak's task and move it to Review: the problem is
gone, and a person confirms that and closes the task. A streak that closes by
its kind's recovery event names that event and links it; one that closes by
its quiet window says the window passed without a failure. The comment is
built only from the streak and the event that closed it, so a redelivered copy
is the same text and the task manager can skip it. A streak that never
escalated has no task, and sends nothing. A request that does not land is
reported, and the recovery stands: the task stays where it is for a person.

**The task arrives ready.** A detector knows what the board's template asks
for: where the problem is, the outcome that ends it (the check passing
again), and what a fix must not do (silence it). So it says so, and the
task passes the definition of ready as it lands rather than waiting on a
paid grooming run that could add nothing the detector does not know. Every
escalated task is sized ``S``: one thing that worked has stopped working.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from mini_app_polis.api.contract import (
    TaskCreateRequest,
    TaskDraft,
    TaskEffort,
    TaskLink,
    TaskSection,
    TaskUpdateRequest,
)
from mini_app_polis.logger import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from . import job_queue, task_dispatch

logger = get_logger()

#: What a detector says one event means.
State = Literal["failing", "recovered"]

#: What :func:`observe` did with a signal. For logs and tests.
Outcome = Literal[
    "opened",  # a first failure, waiting under its kind's rule
    "bumped",  # a further failure of a streak already escalated or remediating
    "remediating",  # a remediation was started instead of a task
    "escalated",  # a task was requested
    "dropped",  # a task request did not land; the next failure retries
    "recovered",  # an escalated streak closed
    "healed",  # a streak closed before it escalated
    "quiet",  # a recovery with no open streak
]

# The contract's limits; detector text is cut to fit rather than refused.
_TITLE_MAX = 500
_TEXT_MAX = 20_000
_EXTERNAL_ID_MAX = 256
_FIELD_MAX = 1000

#: Every escalated task's size: one thing that worked has stopped working.
EFFORT: TaskEffort = "S"

#: The column a recovered streak's task moves to, for a person to confirm.
REVIEW_SECTION = "Review"


@dataclass(frozen=True)
class Signal:
    """One event, as a detector reads it."""

    kind: str
    key: str
    state: State
    title: str
    where: str | None = None
    link: str | None = None
    details: str | None = None
    #: The template's ``Done when``: the observable outcome that ends it.
    done_when: str | None = None
    #: The template's ``Constraints``: what a fix must not do.
    constraints: str | None = None
    sections: tuple[tuple[str, str], ...] = ()
    #: Whether this event may be remediated (a re-run is pointless on a run
    #: that is itself a re-run).
    remediable: bool = False
    #: What a remediation needs to act on this event (a repo, a run id).
    context: dict[str, Any] = field(default_factory=dict)


#: Starts a remediation for a signal. Returns whether it started.
Remediation = Callable[[Signal, Settings], Awaitable[bool]]


@dataclass(frozen=True)
class Rule:
    """When a kind's streak becomes a task."""

    #: Failures in one streak before it escalates.
    escalate_after: int = 1
    #: Tried before escalating; at most ``max_remediations`` per streak.
    remediation: Remediation | None = None
    max_remediations: int = 0
    #: For a kind with no recovery event: a streak with no failure for this
    #: long is over, and the next failure starts a new one.
    quiet_window: dt.timedelta | None = None


def _cut(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def external_id_for(kind: str, key: str, streak_id: Any) -> str:
    """The task's key: one task per streak, never two."""
    suffix = f"#{streak_id}"
    return _cut(f"{kind}.{key}", _EXTERNAL_ID_MAX - len(suffix)) + suffix


def request_for(
    signal: Signal, *, external_id: str, failures: int, opened_at: dt.datetime
) -> TaskCreateRequest:
    """The task an escalated streak asks for."""
    sections = [
        TaskSection(label=label, text=_cut(text_, _TEXT_MAX))
        for label, text_ in signal.sections
        if text_
    ]
    sections.append(
        TaskSection(
            label="Streak",
            text=f"{failures} failure(s) since {opened_at:%Y-%m-%d %H:%M} UTC",
        )
    )
    links = []
    if signal.link and signal.link.startswith("https://"):
        links.append(TaskLink(text="Latest failure", url=signal.link))
    return TaskCreateRequest.model_validate(
        {
            "source": signal.kind,
            "external_id": external_id,
            "id": None,
            "draft": TaskDraft(
                title=_cut(signal.title, _TITLE_MAX),
                where=_cut(signal.where, 200) if signal.where else None,
                body=_cut(signal.details, _TEXT_MAX) if signal.details else None,
                done_when=(
                    _cut(signal.done_when, _FIELD_MAX) if signal.done_when else None
                ),
                constraints=(
                    _cut(signal.constraints, _FIELD_MAX) if signal.constraints else None
                ),
                effort=EFFORT,
                sections=sections,
                links=links,
                labels=[],
                needs_review=False,
                due_on=None,
            ),
        }
    )


def recovery_comment(signal: Signal) -> str:
    """The comment for a task whose streak the event ``signal`` closed."""
    lines = [f"Recovered: {signal.title}."]
    if signal.details:
        lines.append(signal.details)
    if signal.link and signal.link.startswith("https://"):
        lines.append(signal.link)
    lines.append("Moved to Review for a person to confirm the fix and close the task.")
    return _cut("\n".join(lines), _TEXT_MAX)


def quiet_comment(signal: Signal, window: dt.timedelta) -> str:
    """The comment for a task whose streak closed by its quiet window."""
    minutes = int(window.total_seconds() // 60)
    return _cut(
        f"Recovered: {minutes} minutes passed without another {signal.key}.\n"
        "Moved to Review for a person to confirm the fix and close the task.",
        _TEXT_MAX,
    )


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


async def _recover(
    session: AsyncSession, signal: Signal, *, settings: Settings
) -> Outcome:
    row = (
        await session.execute(
            text(
                """
                UPDATE signal_streaks SET closed_at = now()
                WHERE kind = :kind AND key = :key AND closed_at IS NULL
                RETURNING escalated_at, failures, task_external_id
                """
            ),
            {"kind": signal.kind, "key": signal.key},
        )
    ).first()
    await session.commit()
    if row is None:
        return "quiet"
    escalated_at, failures, task_external_id = row
    if escalated_at is None:
        logger.info(
            "signals: %s %s healed after %d failure(s); nothing reached the board",
            signal.kind,
            signal.key,
            failures,
        )
        return "healed"
    logger.info("signals: %s %s recovered", signal.kind, signal.key)
    if task_external_id:
        await _request_review(
            signal,
            external_id=task_external_id,
            comment=recovery_comment(signal),
            settings=settings,
        )
    return "recovered"


async def _close_if_quiet(
    session: AsyncSession,
    signal: Signal,
    window: dt.timedelta,
    *,
    settings: Settings,
) -> None:
    """Close the open streak if it has gone ``window`` without a failure.

    Closed as of the end of its window, not now: that is when it was over.
    """
    row = (
        await session.execute(
            text(
                """
                UPDATE signal_streaks
                SET closed_at = last_seen_at + make_interval(secs => :secs)
                WHERE kind = :kind AND key = :key AND closed_at IS NULL
                  AND last_seen_at < now() - make_interval(secs => :secs)
                RETURNING escalated_at, failures, task_external_id
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
        return
    escalated_at, failures, task_external_id = row
    logger.info(
        "signals: %s %s went quiet after %d failure(s)%s",
        signal.kind,
        signal.key,
        failures,
        "" if escalated_at is not None else "; nothing reached the board",
    )
    if escalated_at is not None and task_external_id:
        await _request_review(
            signal,
            external_id=task_external_id,
            comment=quiet_comment(signal, window),
            settings=settings,
        )


async def _escalate(
    session: AsyncSession,
    signal: Signal,
    *,
    streak_id: Any,
    failures: int,
    opened_at: dt.datetime,
    settings: Settings,
) -> Outcome:
    external_id = external_id_for(signal.kind, signal.key, streak_id)
    claimed = (
        await session.execute(
            text(
                """
                UPDATE signal_streaks
                SET escalated_at = now(), task_external_id = :external_id
                WHERE id = :id AND escalated_at IS NULL
                RETURNING id
                """
            ),
            {"id": streak_id, "external_id": external_id},
        )
    ).first()
    await session.commit()
    if claimed is None:
        return "bumped"

    request = request_for(
        signal, external_id=external_id, failures=failures, opened_at=opened_at
    )
    try:
        await task_dispatch.dispatch_task(request, settings=settings)
    except task_dispatch.DispatchError as exc:
        # Release the claim, so the next failure of this streak asks again.
        await session.execute(
            text(
                "UPDATE signal_streaks SET escalated_at = NULL, "
                "task_external_id = NULL WHERE id = :id"
            ),
            {"id": streak_id},
        )
        await session.commit()
        await job_queue.report_dropped(
            exc.detail or f"could not enqueue {external_id}",
            label="signals",
            heading="Signal not escalated",
            settings=settings,
        )
        return "dropped"
    logger.info("signals: %s escalated as %s", signal.key, external_id)
    return "escalated"


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
        await _close_if_quiet(session, signal, rule.quiet_window, settings=settings)

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
                RETURNING id, failures, remediations, escalated_at, opened_at
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
    streak_id, failures, remediations, escalated_at, opened_at = row

    if escalated_at is not None:
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
    )
