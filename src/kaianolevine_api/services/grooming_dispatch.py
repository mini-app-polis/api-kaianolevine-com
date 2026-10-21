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

from mini_app_polis.api.contract import GroomingJobRequest, GroomingProposal

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "MESSAGE_VERSION",
    "TYPE_GROOM",
    "TYPE_PROPOSAL",
    "DispatchError",
    "dispatch_job",
    "dispatch_proposal",
    "job_message",
    "proposal_message",
]

#: Schema version carried on both messages. A consumer refuses a version it
#: does not speak rather than misreading it.
MESSAGE_VERSION = 1

#: The one message type on task-groomer-jobs.
TYPE_GROOM = "task.groom"

#: A proposal, on task-manager-jobs.fifo beside task.create and task.sweep.
TYPE_PROPOSAL = "task.groom-proposal"


def job_message(job: GroomingJobRequest) -> dict[str, Any]:
    """Render one job as a ``TYPE_GROOM`` queue message."""
    return {
        "type": TYPE_GROOM,
        "version": MESSAGE_VERSION,
        "payload": job.model_dump(mode="json", exclude_none=True),
    }


def proposal_message(proposal: GroomingProposal) -> dict[str, Any]:
    """Render one proposal as a ``TYPE_PROPOSAL`` queue message."""
    return {
        "type": TYPE_PROPOSAL,
        "version": MESSAGE_VERSION,
        "payload": proposal.model_dump(mode="json", exclude_none=True),
    }


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
