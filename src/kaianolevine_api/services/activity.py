"""The running list — what changed, and what broke — wired to this service.

The machinery lives in ``mini_app_polis.activity``, shared with the fleet's
other APIs: the SQLAlchemy listeners that tally committed changes, the
middleware that posts one change summary per request to ``activity`` and
every fault to ``errors``, and the rules that keep row data out of the
channel. Its module docstring is the account of what is and is not seen.
Importing this module imports that one, which is what registers the
listeners.

What stays here is this service's policy, read from ``Settings`` on every
request: ``NOTIFY_*`` switches, the per-environment event allow-list
(``DISCORD_NOTIFY_EVENTS``), suppressed tables and excluded paths, its
machine callers (``request.state.caller_kind``, stamped by ``auth``), and
delivery through ``services.discord`` so webhooks resolve from
``Settings``. Every message's footer names ``api-kaianolevine-com`` and
``settings.ENVIRONMENT``. Only production posts change summaries and
faults, as in api-deejaytools: the channels are production's record, and a
development process logs what it changed and what broke instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from mini_app_polis import activity as _activity
from mini_app_polis.activity import (
    BULK,
    CREATED,
    DELETED,
    UPDATED,
    Recorder,
    record_fault_detail,
)

#: The shared module's live delivery tasks, re-exported so a test can drain
#: them from here.
from mini_app_polis.activity import _in_flight as _in_flight
from mini_app_polis.environment import is_production

from ..config import Settings, get_settings
from . import discord, fault_signals

__all__ = [
    "BULK",
    "CREATED",
    "DATA_CHANGED",
    "DELETED",
    "FAULT",
    "NOTIFY_EVENTS",
    "UPDATED",
    "Recorder",
    "activity_middleware",
    "emit_change",
    "emit_fault",
    "event_enabled",
    "is_notifiable_fault",
    "record_fault_detail",
]

#: The running list's message kinds, as ``DISCORD_NOTIFY_EVENTS`` names them:
#: the summary of what a request committed, and a fault (a 5xx, or a 4xx to
#: a machine caller).
DATA_CHANGED = "data_changed"
FAULT = "fault"
NOTIFY_EVENTS = (DATA_CHANGED, FAULT)


def event_enabled(event: str, settings: Settings) -> bool:
    """Whether ``settings.ENVIRONMENT`` posts ``event`` to Discord.

    An environment with no entry in ``DISCORD_NOTIFY_EVENTS`` posts every
    event. The ``NOTIFY_*`` switches are a separate gate.
    """
    lists = {k.lower(): v for k, v in settings.DISCORD_NOTIFY_EVENTS.items()}
    allowed = lists.get(settings.ENVIRONMENT.lower())
    return allowed is None or event in allowed


def _is_machine(request: Any) -> bool:
    return getattr(request.state, "caller_kind", None) == "machine"


def _config(settings: Settings) -> _activity.ActivityConfig:
    """This service's feed policy, as ``Settings`` has it right now."""

    async def send(channel: str, payload: dict[str, Any], context: str) -> bool:
        return await discord.send_message(
            settings=settings, payload=payload, channel=channel, context=context
        )

    return _activity.ActivityConfig(
        # Named because api-deejaytools posts to the same channels.
        service="api-kaianolevine-com",
        environment=settings.ENVIRONMENT,
        suppressed_tables=frozenset(settings.NOTIFY_SUPPRESSED_TABLES),
        excluded_paths=tuple(settings.NOTIFY_EXCLUDED_PATHS),
        report_changes=settings.NOTIFY_DATA_CHANGES
        and event_enabled(DATA_CHANGED, settings),
        report_faults=settings.NOTIFY_FAULTS and event_enabled(FAULT, settings),
        is_machine=_is_machine if settings.NOTIFY_MACHINE_CLIENT_ERRORS else None,
        send=send,
        label=True,
    )


def is_notifiable_fault(
    status_code: int, caller_kind: str | None, settings: Settings
) -> bool:
    """Whether this response is a fault worth reporting, under ``settings``."""
    return _activity.is_notifiable_fault(
        status_code,
        machine=settings.NOTIFY_MACHINE_CLIENT_ERRORS and caller_kind == "machine",
        report_faults=settings.NOTIFY_FAULTS and event_enabled(FAULT, settings),
    )


def _excluded(path: str, settings: Settings) -> bool:
    """Whether this path is outside the feed entirely."""
    return _activity.is_excluded(path, settings.NOTIFY_EXCLUDED_PATHS)


def _fault_detail(exc: BaseException) -> str:
    """The exception's type and Sentry id, never its message (reports it)."""
    return _activity.fault_detail(exc)


async def emit_change(
    *,
    settings: Settings,
    method: str,
    path: str,
    actor: str,
    summary: str,
    legend: bool,
) -> None:
    """Post one message summarising what a request committed."""
    await _activity.emit_change(
        _config(settings),
        method=method,
        path=path,
        actor=actor,
        summary=summary,
        legend=legend,
    )


async def emit_fault(
    *,
    settings: Settings,
    method: str,
    path: str,
    actor: str,
    status_code: int,
    detail: str | None,
) -> None:
    """Post one message about a request that failed on this side of the line."""
    await _activity.emit_fault(
        _config(settings),
        method=method,
        path=path,
        actor=actor,
        status_code=status_code,
        detail=detail,
    )


_middleware = _activity.activity_middleware(lambda: _config(get_settings()))


def _observe_fault(request: Request, status_code: int) -> None:
    """Hand a 5xx to the signal engine, after the response. Production only."""
    settings = get_settings()
    if not is_production() or _excluded(request.url.path, settings):
        return
    signal = fault_signals.signal_for(
        request.method, fault_signals.route_template(request.scope), status_code
    )
    _activity.dispatch(fault_signals.observe(signal, settings))


async def activity_middleware(request: Request, call_next: Any) -> Any:
    """Open a recorder for the request, and report what it did on the way out.

    Sits outside the routers and outside the exception handlers that turn
    an ``HTTPException`` into a response, so it sees the status that
    actually went on the wire. It sits *inside* Starlette's server-error
    middleware, so an unhandled exception reaches here as a raise and is
    reported and re-raised unchanged.

    A 5xx is also an api-fault signal (services/fault_signals.py), observed
    in the background so the response does not wait on it.
    """
    try:
        response = await _middleware(request, call_next)
    except Exception:
        _observe_fault(request, 500)
        raise
    if response.status_code >= 500:
        _observe_fault(request, response.status_code)
    return response
