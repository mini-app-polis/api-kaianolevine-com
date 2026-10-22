"""This service's own 5xx answers as a source of signals.

The detector sits in the activity middleware (services/activity.py), which
already sees the status that went on the wire, including an unhandled
exception, which reaches it as a raise and is answered 500. A 5xx from a
path outside the running list (``NOTIFY_EXCLUDED_PATHS``) is not a signal
either.

A fault is keyed by method, route template and status
(``"GET /v1/sets/{set_id} 500"``), never the URL, so one broken route is one
streak whatever ids it was called with. A 5xx has no recovery event, so the
rule closes a streak by a quiet window: ``SIGNAL_FAULT_ESCALATE_AFTER``
faults, each within ``SIGNAL_FAULT_WINDOW_MINUTES`` of the last, escalate;
a route quiet for the window has recovered, and its next fault starts afresh.

Observing writes to the database, so it runs after the response, on its own
session, and never raises: a fault must not become a slower fault, or a
second one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

from mini_app_polis.logger import get_logger

from ..config import Settings
from ..database import get_sessionmaker
from . import signals
from .signals import Rule, Signal

logger = get_logger()

KIND = "api-fault"

#: The service whose faults these are; the task's ``Where``.
SERVICE = "api-kaianolevine-com"

#: A request that matched no route has no template to key on.
UNMATCHED = "<unmatched>"


def route_template(scope: Mapping[str, Any]) -> str:
    """The template of the route a request matched, or :data:`UNMATCHED`."""
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) and path else UNMATCHED


def signal_for(method: str, route: str, status_code: int) -> Signal:
    """The signal one 5xx answer carries."""
    key = f"{method} {route} {status_code}"
    return Signal(
        kind=KIND,
        key=key,
        state="failing",
        title=f"API: {method} {route} answering {status_code}",
        where=SERVICE,
        details=(
            f"{SERVICE} answered {status_code} to {method} {route}. The "
            "errors channel and Sentry hold each fault's exception type and id."
        ),
        done_when=f"{method} {route} no longer answers {status_code}.",
        constraints=(
            "Fix the cause. Do not catch the error only to answer a different status."
        ),
        sections=(
            ("Route", f"{method} {route}"),
            ("Status", str(status_code)),
        ),
    )


def rule(settings: Settings) -> Rule:
    """A fault's rule: N within a window, and a quiet window closes it."""
    return Rule(
        escalate_after=settings.SIGNAL_FAULT_ESCALATE_AFTER,
        quiet_window=dt.timedelta(minutes=settings.SIGNAL_FAULT_WINDOW_MINUTES),
    )


async def observe(signal: Signal, settings: Settings) -> None:
    """Record one fault on a session of its own. Never raises."""
    try:
        async with get_sessionmaker(settings.DATABASE_URL)() as session:
            outcome = await signals.observe(
                session, signal, rule=rule(settings), settings=settings
            )
    except Exception:  # noqa: BLE001 — see the module docstring
        logger.exception("signals: could not observe %s", signal.key)
        return
    logger.info("signals: %s %s -> %s", signal.kind, signal.key, outcome)
