"""Letting Railway sleep the service while nobody is using it.

Railway's serverless mode stops a service once it has sent nothing for 5 to
10 minutes, and wakes it on the next request. Anything that talks to the
database or another service on its own keeps it awake: an open pooled
connection, the Discord outbox drain, the GitHub dashboard refresh, the
Spotify token check. With sleeping on (``enabled``):

- the database engine keeps no connection open between requests
  (database.py);
- those background loops skip their work once the service has had no
  request for ``QUIET_AFTER_SECONDS``, and carry on at the next one.

A sleeping service's container is stopped, so the request that wakes it
boots a fresh process, which warms the dashboard and drains the outbox as
every boot does.

On by default in development only, where nobody is using the service most of
the time; ``SLEEP_WHEN_IDLE`` turns it on or off anywhere. Railway's own
setting (Serverless, on the service) is what actually sleeps it.
"""

from __future__ import annotations

import time
from typing import Any

from .config import Settings

#: How long after the last request the background work goes quiet. Railway
#: then sleeps the service 5 to 10 minutes later.
QUIET_AFTER_SECONDS = 180.0

_last_request = time.monotonic()


def enabled(settings: Settings) -> bool:
    """Whether the service is set up to sleep: SLEEP_WHEN_IDLE, else development."""
    if settings.SLEEP_WHEN_IDLE is not None:
        return settings.SLEEP_WHEN_IDLE
    return settings.ENVIRONMENT.lower() == "development"


def touch(now: float | None = None) -> None:
    """Record a request now."""
    global _last_request
    _last_request = time.monotonic() if now is None else now


def quiet(settings: Settings, now: float | None = None) -> bool:
    """Whether background work should hold off: sleeping is on and no request
    has arrived for ``QUIET_AFTER_SECONDS``."""
    if not enabled(settings):
        return False
    current = time.monotonic() if now is None else now
    return current - _last_request > QUIET_AFTER_SECONDS


class ActivityMiddleware:
    """Records every HTTP request, so ``quiet`` knows the service is in use."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            touch()
        await self.app(scope, receive, send)
