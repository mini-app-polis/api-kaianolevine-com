"""Handing an agent run's result to task-manager-cog.

The agent's workflow posts how its run ended; the result goes on
task-manager-jobs.fifo, in line with everything else that writes to the
board, and task-manager-cog moves the task. The payload is the run as
recorded, so the cog reads the task, the pull request and the cost from one
definition.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.api.contract import AgentRun

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "MESSAGE_VERSION",
    "TYPE_RESULT",
    "DispatchError",
    "dispatch_result",
    "result_message",
]

#: Schema version carried on the message. A consumer refuses a version it
#: does not speak rather than misreading it.
MESSAGE_VERSION = 1

#: A run's result, on task-manager-jobs.fifo beside task.create and task.sweep.
TYPE_RESULT = "task.agent-result"


def result_message(run: AgentRun) -> dict[str, Any]:
    """Render one ended run as a ``TYPE_RESULT`` queue message."""
    return {
        "type": TYPE_RESULT,
        "version": MESSAGE_VERSION,
        "payload": run.model_dump(mode="json", exclude_none=True),
    }


async def dispatch_result(run: AgentRun, *, settings: Settings) -> dict:
    """Enqueue one run's result for the task manager. Raises DispatchError."""
    return await job_queue.enqueue(
        result_message(run),
        f"agent result for task {run.task_gid}",
        cog="task-manager",
        label="agent result",
        # A dropped result is a paid run whose pull request never reaches
        # the board.
        heading="Agent result not dispatched",
        settings=settings,
    )
