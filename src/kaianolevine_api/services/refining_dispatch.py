"""Handing grooming work between task-manager-cog and task-groomer-cog.

Two messages, one in each direction, and this API carries both so neither
cog holds the other's queue:

- A **job**: task-manager-cog asks for one task to be groomed, and the job
  goes on task-groomer-jobs. The groomer reads the task and the code it
  names, and proposes what the task should say.
- A **proposal**: the groomer hands its proposal back, and it goes on
  task-manager-jobs.fifo, in line with everything else that writes to the
  board. task-manager-cog applies it; the groomer never writes to the board.

Each payload is the contract model as the API validated it, so both
consumers validate against the same definition.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.api.contract import (
    GroomingJobRequest,
    GroomingProposal,
    TaskGroomMessage,
    TaskGroomProposalMessage,
)

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "DispatchError",
    "dispatch_job",
    "dispatch_proposal",
    "job_message",
    "proposal_message",
]


def job_message(job: GroomingJobRequest) -> dict[str, Any]:
    """Render one job as a ``task.groom`` queue message."""
    return TaskGroomMessage(payload=job).model_dump(mode="json", exclude_none=True)


def proposal_message(proposal: GroomingProposal) -> dict[str, Any]:
    """Render one proposal as a ``task.groom-proposal`` queue message."""
    return TaskGroomProposalMessage(payload=proposal).model_dump(
        mode="json", exclude_none=True
    )


async def dispatch_job(job: GroomingJobRequest, *, settings: Settings) -> dict:
    """Enqueue one grooming job. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        job_message(job),
        f"grooming of task {job.task.task_gid}",
        cog="task-groomer",
        label="grooming dispatch",
        heading="Grooming not dispatched",
        settings=settings,
    )


async def dispatch_proposal(proposal: GroomingProposal, *, settings: Settings) -> dict:
    """Enqueue one proposal for the task manager. Raises DispatchError."""
    return await job_queue.enqueue(
        proposal_message(proposal),
        f"grooming proposal for task {proposal.task_gid}",
        cog="task-manager",
        label="grooming proposal",
        # A dropped proposal is a paid run whose result never reaches the
        # board.
        heading="Grooming proposal not dispatched",
        settings=settings,
    )
