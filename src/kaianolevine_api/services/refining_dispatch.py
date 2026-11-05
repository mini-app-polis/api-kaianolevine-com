"""Handing refining work between task-manager-cog and task-refiner-cog.

Two messages, one in each direction, and this API carries both so neither
cog holds the other's queue:

- A **job**: task-manager-cog asks for one task to be refined, and the job
  goes on task-refiner-jobs. The refiner reads the task and the code it
  names, and proposes what the task should say.
- A **proposal**: the refiner hands its proposal back, and it goes on
  task-manager-jobs.fifo, in line with everything else that writes to the
  board. task-manager-cog applies it; the refiner never writes to the board.

Each payload is the contract model as the API validated it, so both
consumers validate against the same definition.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.api.contract import (
    RefiningJobRequest,
    RefiningProposal,
    TaskGroomMessage,
    TaskGroomProposalMessage,
    TaskRefineMessage,
    TaskRefineProposalMessage,
)

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "DispatchError",
    "dispatch_grooming_job",
    "dispatch_grooming_proposal",
    "dispatch_job",
    "dispatch_proposal",
    "grooming_job_message",
    "grooming_proposal_message",
    "job_message",
    "proposal_message",
]


def job_message(job: RefiningJobRequest) -> dict[str, Any]:
    """Render one job as a ``task.refine`` queue message."""
    return TaskRefineMessage(payload=job).model_dump(mode="json", exclude_none=True)


def proposal_message(proposal: RefiningProposal) -> dict[str, Any]:
    """Render one proposal as a ``task.refine-proposal`` queue message."""
    return TaskRefineProposalMessage(payload=proposal).model_dump(
        mode="json", exclude_none=True
    )


async def dispatch_job(job: RefiningJobRequest, *, settings: Settings) -> dict:
    """Enqueue one refining job. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        job_message(job),
        f"refining of task {job.task.task_gid}",
        cog="task-refiner",
        label="refining dispatch",
        heading="Refining not dispatched",
        settings=settings,
    )


async def dispatch_proposal(proposal: RefiningProposal, *, settings: Settings) -> dict:
    """Enqueue one proposal for the task manager. Raises DispatchError."""
    return await job_queue.enqueue(
        proposal_message(proposal),
        f"refining proposal for task {proposal.task_gid}",
        cog="task-manager",
        label="refining proposal",
        # A dropped proposal is a paid run whose result never reaches the
        # board.
        heading="Refining proposal not dispatched",
        settings=settings,
    )


# The grooming names. A job asked for through /v1/tasks/groomings/jobs is
# task-manager-cog before it moved to refining, so it goes to the groomer
# that is still running, as a ``task.groom`` on task-groomer-jobs; that
# groomer hands its proposal back through /v1/tasks/groomings/proposals,
# which a task manager that has not moved reads as ``task.groom-proposal``.
# Removed once every cog has moved to refining.


def grooming_job_message(job: RefiningJobRequest) -> dict[str, Any]:
    """Render one job as a ``task.groom`` queue message."""
    return TaskGroomMessage(payload=job).model_dump(mode="json", exclude_none=True)


def grooming_proposal_message(proposal: RefiningProposal) -> dict[str, Any]:
    """Render one proposal as a ``task.groom-proposal`` queue message."""
    return TaskGroomProposalMessage(payload=proposal).model_dump(
        mode="json", exclude_none=True
    )


async def dispatch_grooming_job(job: RefiningJobRequest, *, settings: Settings) -> dict:
    """Enqueue one job for the groomer. Raises DispatchError."""
    return await job_queue.enqueue(
        grooming_job_message(job),
        f"grooming of task {job.task.task_gid}",
        cog="task-groomer",
        label="grooming dispatch",
        heading="Grooming not dispatched",
        settings=settings,
    )


async def dispatch_grooming_proposal(
    proposal: RefiningProposal, *, settings: Settings
) -> dict:
    """Enqueue one groomer's proposal for the task manager. Raises DispatchError."""
    return await job_queue.enqueue(
        grooming_proposal_message(proposal),
        f"grooming proposal for task {proposal.task_gid}",
        cog="task-manager",
        label="grooming proposal",
        heading="Grooming proposal not dispatched",
        settings=settings,
    )
