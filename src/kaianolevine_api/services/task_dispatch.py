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

The mechanics of sending, and of insisting that a send landed, are shared
with every dispatcher; see :mod:`.job_queue`.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.api.contract import TaskCreateMessage, TaskCreateRequest

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "DispatchError",
    "as_message",
    "dispatch_task",
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
