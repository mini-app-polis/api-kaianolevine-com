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

A change to a task already on the board is a ``task.update`` message,
naming the task by the ``external_id`` it was created under, with a comment
and, optionally, a column to move it to. It is sent on the same queue and in
the same group, so it never overtakes the ``task.create`` that made the task.
The FIFO queue deduplicates it on its body, and the cog skips a comment the
task already has, so a repeated update changes nothing.

A sweep of the board asked for on demand is the same ``task.sweep`` message
the cog's hourly schedule enqueues, on the same queue and in the same group,
so it never runs beside a job creating a task. A request with no flags sends
the schedule's body exactly; one with ``groom`` or ``agents`` adds a payload
carrying both flags, which the cog reads as making that sweep the one for
grooming or agent dispatch. Two requests can send the same body, so each
carries a deduplication id of its own: asking twice within the queue's
five-minute window runs two sweeps, one after the other, rather than one.

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
    TaskUpdateMessage,
    TaskUpdateRequest,
)

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "DispatchError",
    "as_message",
    "dispatch_sweep",
    "dispatch_task",
    "dispatch_task_update",
    "sweep_message",
    "update_message",
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


def update_message(request: TaskUpdateRequest) -> dict[str, Any]:
    """Render one update as a ``task.update`` queue message."""
    return TaskUpdateMessage(payload=request).model_dump(mode="json", exclude_none=True)


async def dispatch_task_update(
    request: TaskUpdateRequest, *, settings: Settings
) -> dict:
    """Enqueue one task update. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        update_message(request),
        f"an update to task {request.external_id}",
        cog="task-manager",
        label="task dispatch",
        heading="Task update not dispatched",
        settings=settings,
    )


def sweep_message(*, groom: bool = False, agents: bool = False) -> dict[str, Any]:
    """A ``task.sweep`` message.

    With no flags it is exactly what the cog's schedule sends: no payload,
    which the contract allows for a sweep with nothing to carry. With either
    flag the payload carries both.
    """
    message = TaskSweepMessage().model_dump(mode="json", exclude={"payload"})
    if groom or agents:
        message["payload"] = {"groom": groom, "agents": agents}
    return message


async def dispatch_sweep(
    *, settings: Settings, groom: bool = False, agents: bool = False
) -> dict:
    """Enqueue one sweep of the board. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        sweep_message(groom=groom, agents=agents),
        "a board sweep",
        cog="task-manager",
        label="task dispatch",
        heading="Sweep not dispatched",
        settings=settings,
        deduplication_id=uuid.uuid4().hex,
    )
