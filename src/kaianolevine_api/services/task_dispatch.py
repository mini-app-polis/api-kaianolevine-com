"""Handing a task request to task-manager-cog.

A source (a cog, or this API itself for a GitHub issue) asks for a task
with a :class:`TaskCreateRequest`, and the request becomes this message's
payload unchanged. task-manager-cog validates it against the same model
from the shared contract, so the two sides cannot disagree about its shape.

The queue is FIFO with one message group: task-manager-cog writes to the
board one job at a time, which is what lets it mint "the highest id plus
one" without two jobs taking the same id.

A request repeated by its source is not deduplicated here. It needs no
claim: the cog looks the source's ``external_id`` up on the board before it
creates anything, so a second job finds the first one's task and stops.

A sweep of the board asked for on demand is the same ``task.sweep`` message
the cog's hourly schedule enqueues, on the same queue and in the same group,
so it never runs beside a job creating a task. Its body never changes, so
each request carries a deduplication id of its own: asking twice within the
queue's five-minute window runs two sweeps, one after the other, rather than
one.

The mechanics of sending, and of insisting that a send landed, are shared
with every dispatcher; see :mod:`.job_queue`.
"""

from __future__ import annotations

import uuid
from typing import Any

from mini_app_polis.api.contract import (
    TaskCreateMessage,
    TaskCreateRequest,
    TaskSweepMessage,
)

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "DispatchError",
    "as_message",
    "dispatch_sweep",
    "dispatch_task",
    "sweep_message",
]


def as_message(request: TaskCreateRequest) -> dict[str, Any]:
    """Render one request as a ``task.create`` queue message."""
    return TaskCreateMessage(payload=request).model_dump(mode="json", exclude_none=True)


async def dispatch_task(request: TaskCreateRequest, *, settings: Settings) -> dict:
    """Enqueue one task request. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        as_message(request),
        f"{request.source} task {request.external_id}",
        cog="task-manager",
        label="task dispatch",
        # A dropped request is work that never reaches the board while its
        # source believes it was asked for.
        heading="Task not dispatched",
        settings=settings,
    )


def sweep_message() -> dict[str, Any]:
    """A ``task.sweep`` message, exactly as the cog's schedule sends it.

    The schedule's body has no payload, which the contract allows for a kind
    with nothing to carry, so the payload is left out here as well.
    """
    return TaskSweepMessage().model_dump(mode="json", exclude={"payload"})


async def dispatch_sweep(*, settings: Settings) -> dict:
    """Enqueue one sweep of the board. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        sweep_message(),
        "a board sweep",
        cog="task-manager",
        label="task dispatch",
        heading="Sweep not dispatched",
        settings=settings,
        deduplication_id=uuid.uuid4().hex,
    )
