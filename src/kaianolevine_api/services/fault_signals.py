"""This service's own 5xx answers as a source of signals.

The detector sits in the activity middleware (services/activity.py), which
already sees the status that went on the wire, including an unhandled
exception, which reaches it as a raise and is answered 500. A 5xx from a
path outside the running list (``NOTIFY_EXCLUDED_PATHS``) is not a signal
either.

A fault is keyed by method, route template and status
(``"GET /v1/sets/{set_id} 500"``), never the URL, so one broken route is one
streak whatever ids it was called with. Every fault is reported: the first
opens a GitHub Issue in this repository, and each further one comments on it
with its time, request id and Sentry event, where those are known.

What closes the Issue depends on whose failure it is:

* **502, 503 and 504** are a dependency failing (an upstream refused, was
  unavailable or timed out). A 5xx has no recovery event, so the Issue
  closes, with a comment, once the route has gone ``SIGNAL_FAULT_QUIET_HOURS``
  without that fault. :func:`sweep_quiet` makes that check; watcher-cog asks
  for it on a schedule (``POST /v1/signals/api-faults/quiet-sweep``), and the
  next fault makes it again.
* **Every other 5xx** (500 above all) is this service's own bug. Its Issue
  never closes on its own: it says to fix it in a pull request that says
  ``Fixes #N``. If the same fault comes back after its Issue was closed, a
  new Issue opens and links the old one.

Both kinds read the Issue's state on a fault, so one a person closed by hand
is treated as fixed too.

Observing writes to the database, so it runs after the response, on its own
session, and never raises: a fault must not become a slower fault, or a
second one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mini_app_polis.logger import get_logger
from sqlalchemy import text

from ..config import Settings
from ..database import get_sessionmaker
from . import signals
from .signals import Rule, Signal

logger = get_logger()

KIND = "api-fault"

#: The service whose faults these are, and the repository their Issues open in.
SERVICE = "api-kaianolevine-com"

#: A request that matched no route has no template to key on.
UNMATCHED = "<unmatched>"

#: A dependency failing: the Issue closes after a quiet period.
DEPENDENCY_STATUSES = frozenset({502, 503, 504})


def route_template(scope: Mapping[str, Any]) -> str:
    """The template of the route a request matched, or :data:`UNMATCHED`."""
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) and path else UNMATCHED


def is_dependency(status_code: int) -> bool:
    """Whether a status is a dependency failing, rather than this service."""
    return status_code in DEPENDENCY_STATUSES


def parse_key(key: str) -> tuple[str, str, int] | None:
    """A fault's key as (method, route template, status), or None."""
    method, _, rest = key.partition(" ")
    route, _, status = rest.rpartition(" ")
    if not method or not route or not status.isdigit():
        return None
    return method, route, int(status)


def _quiet(settings: Settings) -> dt.timedelta:
    return dt.timedelta(hours=settings.SIGNAL_FAULT_QUIET_HOURS)


def signal_for(
    method: str,
    route: str,
    status_code: int,
    *,
    quiet_hours: int,
    at: dt.datetime | None = None,
    request_id: str | None = None,
    sentry_event_id: str | None = None,
) -> Signal:
    """The signal one 5xx answer carries.

    ``at``, ``request_id`` and ``sentry_event_id`` are this fault's own: the
    Issue it opens, or the comment it posts, names them where they are known.
    """
    key = f"{method} {route} {status_code}"
    when = at or dt.datetime.now(dt.UTC)
    evidence = (
        ("At", f"{when:%Y-%m-%d %H:%M:%S} UTC"),
        ("Request id", request_id or ""),
        ("Sentry event", sentry_event_id or ""),
    )
    common: dict[str, Any] = {
        "kind": KIND,
        "key": key,
        "state": "failing",
        "title": f"API: {method} {route} answering {status_code}",
        "where": SERVICE,
        "sections": (("Route", f"{method} {route}"), ("Status", str(status_code))),
        "context": {"status": status_code},
        "evidence": evidence,
    }
    if is_dependency(status_code):
        return Signal(
            **common,
            details=(
                f"{SERVICE} answered {status_code} to {method} {route}: something "
                "it depends on failed, was unavailable or timed out. The errors "
                "channel and Sentry hold each fault."
            ),
            done_when=(
                f"{method} {route} goes {quiet_hours} hours without answering "
                f"{status_code}. This Issue then closes itself, with a comment."
            ),
            constraints=(
                "If the dependency's failure is ours to handle, handle it. Do not "
                "catch the error only to answer a different status."
            ),
        )
    return Signal(
        **common,
        details=(
            f"{SERVICE} answered {status_code} to {method} {route}: a bug in "
            "this service. The errors channel and Sentry hold each fault's "
            "exception type and id."
        ),
        done_when=(
            f"A merged pull request fixes the cause, so {method} {route} no "
            f"longer answers {status_code}, and closes this Issue."
        ),
        constraints=(
            "Fix the cause. Do not catch the error only to answer a different "
            "status. This Issue never closes on its own."
        ),
        opened_comment=(
            "Fix this in a pull request whose description says `Fixes #{number}`, "
            "so merging it closes this Issue. Nothing else closes it. If this "
            "fault comes back after it is closed, a new Issue opens and links "
            "this one."
        ),
    )


def rule(status_code: int, settings: Settings) -> Rule:
    """A fault's rule: every fault reports; who closes it depends on the status."""
    if is_dependency(status_code):
        return Rule(quiet_window=_quiet(settings), reopen_closed_issue=True)
    return Rule(reopen_closed_issue=True)


async def observe(signal: Signal, settings: Settings) -> None:
    """Record one fault on a session of its own. Never raises."""
    try:
        status = int(signal.context["status"])
        async with get_sessionmaker(settings.DATABASE_URL)() as session:
            outcome = await signals.observe(
                session, signal, rule=rule(status, settings), settings=settings
            )
    except Exception:  # noqa: BLE001 — see the module docstring
        logger.exception("signals: could not observe %s", signal.key)
        return
    logger.info("signals: %s %s -> %s", signal.kind, signal.key, outcome)


@dataclass(frozen=True)
class SweepResult:
    """What one quiet sweep found and did."""

    #: Open dependency-fault streaks quiet for the window.
    quiet: int
    #: Of those, the streaks this sweep closed (an Issue, where it had one,
    #: was commented and closed).
    closed: int


async def sweep_quiet(settings: Settings) -> SweepResult:
    """Close every dependency fault that has gone the quiet window unrepeated.

    A 500's streak is never closed here, however long it is quiet. Raises
    only when the database cannot be read; one streak that cannot be closed
    is logged and left for the next sweep.
    """
    window = _quiet(settings)
    async with get_sessionmaker(settings.DATABASE_URL)() as session:
        keys = (
            await session.execute(
                text(
                    """
                    SELECT key FROM signal_streaks
                    WHERE kind = :kind AND closed_at IS NULL
                      AND last_seen_at < now() - make_interval(secs => :secs)
                    ORDER BY last_seen_at
                    """
                ),
                {"kind": KIND, "secs": window.total_seconds()},
            )
        ).scalars()
        quiet = 0
        closed = 0
        for key in list(keys):
            parsed = parse_key(key)
            if parsed is None or not is_dependency(parsed[2]):
                continue
            quiet += 1
            method, route, status = parsed
            signal = signal_for(
                method, route, status, quiet_hours=settings.SIGNAL_FAULT_QUIET_HOURS
            )
            try:
                if await signals.close_if_quiet(
                    session, signal, window, settings=settings
                ):
                    closed += 1
            except Exception:  # noqa: BLE001 — the next sweep tries again
                await session.rollback()
                logger.exception("signals: quiet sweep could not close %s", key)
    logger.info("signals: quiet sweep closed %d of %d quiet fault(s)", closed, quiet)
    return SweepResult(quiet=quiet, closed=closed)
