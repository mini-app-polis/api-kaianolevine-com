"""The running list — what changed, and what broke — wired to this service.

The machinery lives in ``mini_app_polis.activity``, shared with the fleet's
other APIs: the SQLAlchemy listeners that tally committed changes, the
middleware that posts one change summary per request to ``activity`` and
every fault to ``errors``, and the rules that keep row data out of the
channel. Its module docstring is the account of what is and is not seen.
Importing this module imports that one, which is what registers the
listeners.

What stays here is this service's policy, read from ``Settings`` on every
request: ``NOTIFY_*`` switches, suppressed tables and excluded paths, its
machine callers (``request.state.caller_kind``, stamped by ``auth``), and
delivery through ``services.discord`` so webhooks resolve from
``Settings``. Messages carry ``settings.ENVIRONMENT`` and no service name,
as they always have.
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

from ..config import Settings, get_settings
from . import discord

__all__ = [
    "BULK",
    "CREATED",
    "DELETED",
    "UPDATED",
    "Recorder",
    "activity_middleware",
    "emit_change",
    "emit_fault",
    "is_notifiable_fault",
    "record_fault_detail",
]


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
        report_changes=settings.NOTIFY_DATA_CHANGES,
        report_faults=settings.NOTIFY_FAULTS,
        is_machine=_is_machine if settings.NOTIFY_MACHINE_CLIENT_ERRORS else None,
        send=send,
    )


def is_notifiable_fault(
    status_code: int, caller_kind: str | None, settings: Settings
) -> bool:
    """Whether this response is a fault worth reporting, under ``settings``."""
    return _activity.is_notifiable_fault(
        status_code,
        machine=settings.NOTIFY_MACHINE_CLIENT_ERRORS and caller_kind == "machine",
        report_faults=settings.NOTIFY_FAULTS,
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


async def activity_middleware(request: Request, call_next: Any) -> Any:
    """Open a recorder for the request, and report what it did on the way out.

    Sits outside the routers and outside the exception handlers that turn
    an ``HTTPException`` into a response, so it sees the status that
    actually went on the wire. It sits *inside* Starlette's server-error
    middleware, so an unhandled exception reaches here as a raise and is
    reported and re-raised unchanged.
    """
    return await _middleware(request, call_next)
