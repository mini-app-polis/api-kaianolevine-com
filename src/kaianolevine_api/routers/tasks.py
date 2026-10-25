"""Asking task-manager-cog to put a task on the board, and its grooming records.

The callers of ``POST /v1/tasks`` are the fleet's sources of tasks. A source
hands over a draft of the work; task-manager-cog adds the board's template,
the id, tags and column, and creates the task. See
``mini_app_polis.api.contract.tasks``.

The ``/v1/tasks/groomings`` routes are task-manager-cog's memory between
sweeps of the board, the month's paid grooming, the hand-offs between
task-manager-cog and task-groomer-cog (services/grooming_dispatch.py), and
the claims task-groomer-cog takes on a job before grooming it. See
``mini_app_polis.api.contract.groomings``.

The ``/v1/tasks/agent-runs`` routes record the agent runs task-manager-cog
dispatches for Ready tasks, their month's spend, and each run's result,
which the run's workflow posts and this API hands to task-manager-cog
(services/agent_dispatch.py). See ``mini_app_polis.api.contract.agent_runs``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from identity.types import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import get_settings
from ..database import get_db_session
from ..schemas import (
    AgentResultAccepted,
    AgentRun,
    AgentRunCreate,
    AgentRunFailure,
    AgentRunResult,
    AgentSpend,
    Envelope,
    GroomingClaimReleased,
    GroomingClaimRequest,
    GroomingClaimResult,
    GroomingJobAccepted,
    GroomingJobRequest,
    GroomingProposal,
    GroomingProposalAccepted,
    GroomingRecord,
    GroomingRecordWrite,
    GroomingRunCreate,
    GroomingRunRecorded,
    GroomingSpend,
    TaskCreateAccepted,
    TaskCreateRequest,
    api_error,
    success_envelope,
)
from ..services import (
    agent_dispatch,
    agent_runs,
    grooming_dispatch,
    groomings,
    job_queue,
    task_dispatch,
)

router = APIRouter()


@router.post(
    "/tasks",
    response_model=Envelope[TaskCreateAccepted],
    status_code=202,
    summary="Ask task-manager-cog for one task on the board",
    description=(
        "Enqueues one task request and acknowledges. The task is created "
        "afterwards, by task-manager-cog. Asking again with the same "
        "external_id makes no second task: the cog finds the first."
    ),
)
async def create_task_request(
    payload: TaskCreateRequest,
    request: Request,
    principal: Principal = Depends(require_scope("tasks.requests.create")),
) -> Envelope[TaskCreateAccepted]:
    """Accept one task request and put it on the queue.

    The enqueue is awaited, not backgrounded, so a dropped request is a 502
    while the caller is still on the line, and one message in the errors
    channel (its fault report), since a cog will not act on the 502.
    """
    settings = get_settings()
    try:
        accepted = await task_dispatch.dispatch_task(payload, settings=settings)
    except task_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The task request was not enqueued: {exc}"
        ) from exc

    data = TaskCreateAccepted(
        message_id=str(accepted.get("message_id") or ""),
        source=payload.source,
        external_id=payload.external_id,
    )
    return success_envelope(data, count=1, total=1)


@router.get(
    "/tasks/groomings",
    response_model=Envelope[list[GroomingRecord]],
    summary="Every task's grooming record",
    description=(
        "What the task manager last made of each task: the fingerprint of the "
        "content it judged, the verdict and its problems, and whether it moved "
        "the task to Ready."
    ),
)
async def list_groomings(
    principal: Principal = Depends(require_scope("tasks.groomings.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[GroomingRecord]]:
    """All records at once: the board is small, and a sweep needs every one."""
    records = await groomings.list_records(session)
    return success_envelope(
        records,
        count=len(records),
        total=len(records),
    )


@router.post(
    "/tasks/groomings",
    response_model=Envelope[GroomingRecord],
    summary="Write one task's grooming record",
    description="Keyed by task_gid: writing a task's record again replaces it.",
)
async def record_grooming(
    payload: GroomingRecordWrite,
    principal: Principal = Depends(require_scope("tasks.groomings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[GroomingRecord]:
    """Replace one task's record with what the sweep made of it now."""
    record = await groomings.write_record(session, payload)
    return success_envelope(record, count=1, total=1)


@router.post(
    "/tasks/groomings/runs",
    response_model=Envelope[GroomingRunRecorded],
    status_code=201,
    summary="Record one paid grooming run",
    description=(
        "One model run's tokens and cost, recorded so the month's spend can "
        "be capped. Recorded whether or not the run produced a proposal."
    ),
)
async def record_grooming_run(
    payload: GroomingRunCreate,
    principal: Principal = Depends(require_scope("tasks.groomings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[GroomingRunRecorded]:
    """Record one paid run, for the month's spend."""
    run = await groomings.record_run(session, payload)
    return success_envelope(run, count=1, total=1)


@router.get(
    "/tasks/groomings/spend",
    response_model=Envelope[GroomingSpend],
    summary="This month's paid grooming, in total",
    description=(
        "The calendar month's paid runs and their cost so far (UTC). The "
        "groomer reads it before each run and stops at its monthly cap."
    ),
)
async def grooming_spend(
    principal: Principal = Depends(require_scope("tasks.groomings.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[GroomingSpend]:
    """The month's paid runs and their cost so far."""
    spend = await groomings.month_spend(session)
    return success_envelope(spend, count=1, total=1)


@router.post(
    "/tasks/groomings/jobs",
    response_model=Envelope[GroomingJobAccepted],
    status_code=202,
    summary="Ask task-groomer-cog to groom one task",
    description=(
        "Enqueues one grooming job and acknowledges. The groomer's proposal "
        "comes back through POST /v1/tasks/groomings/proposals."
    ),
)
async def request_grooming(
    payload: GroomingJobRequest,
    request: Request,
    principal: Principal = Depends(require_scope("tasks.groomings.write")),
) -> Envelope[GroomingJobAccepted]:
    """Put one job on the groomer's queue; a dropped job is a 502."""
    settings = get_settings()
    try:
        accepted = await grooming_dispatch.dispatch_job(payload, settings=settings)
    except grooming_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The grooming job was not enqueued: {exc}"
        ) from exc
    data = GroomingJobAccepted(
        message_id=str(accepted.get("message_id") or ""),
        task_gid=payload.task.task_gid,
    )
    return success_envelope(data, count=1, total=1)


@router.post(
    "/tasks/groomings/claims",
    response_model=Envelope[GroomingClaimResult],
    summary="Claim one grooming job before grooming it",
    description=(
        "Keyed on the task and the fingerprint of the content the job was sent "
        "for. claimed is true for the first caller and false while the claim "
        "is held, so a job delivered twice is groomed once. A claim given back "
        "through POST /v1/tasks/groomings/claims/release can be taken again."
    ),
)
async def claim_grooming(
    payload: GroomingClaimRequest,
    principal: Principal = Depends(require_scope("tasks.groomings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[GroomingClaimResult]:
    """Claim the job; a claim already held is an answer, not an error."""
    result = await groomings.claim(session, payload)
    return success_envelope(result, count=1, total=1)


@router.post(
    "/tasks/groomings/claims/release",
    response_model=Envelope[GroomingClaimReleased],
    summary="Give a grooming claim back after a failed run",
    description=(
        "Marks a held claim released, so the next send of the same content can "
        "claim it. released is false when no claim was held."
    ),
)
async def release_grooming_claim(
    payload: GroomingClaimRequest,
    principal: Principal = Depends(require_scope("tasks.groomings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[GroomingClaimReleased]:
    """Release the claim, if one is held."""
    result = await groomings.release_claim(session, payload)
    return success_envelope(result, count=1, total=1)


@router.post(
    "/tasks/groomings/proposals",
    response_model=Envelope[GroomingProposalAccepted],
    status_code=202,
    summary="Hand a groomer's proposal to task-manager-cog",
    description=(
        "Enqueues the proposal on task-manager-cog's queue, which applies it "
        "to the board. The groomer itself never writes to the board."
    ),
)
async def submit_grooming_proposal(
    payload: GroomingProposal,
    request: Request,
    principal: Principal = Depends(require_scope("tasks.groomings.write")),
) -> Envelope[GroomingProposalAccepted]:
    """Put one proposal on the task manager's queue; a dropped one is a 502."""
    settings = get_settings()
    try:
        accepted = await grooming_dispatch.dispatch_proposal(payload, settings=settings)
    except grooming_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The grooming proposal was not enqueued: {exc}"
        ) from exc
    data = GroomingProposalAccepted(
        message_id=str(accepted.get("message_id") or ""),
        task_gid=payload.task_gid,
    )
    return success_envelope(data, count=1, total=1)


@router.post(
    "/tasks/agent-runs",
    response_model=Envelope[AgentRun],
    status_code=201,
    summary="Record an agent run about to be started",
    description=(
        "Written by task-manager-cog before it starts a run. A repository "
        "with a run in flight is given no second one: that is a 409."
    ),
)
async def record_agent_run(
    payload: AgentRunCreate,
    principal: Principal = Depends(require_scope("tasks.agent-runs.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[AgentRun]:
    """Record one run; refuse a second in flight in the same repository."""
    try:
        run = await agent_runs.create_run(session, payload)
    except agent_runs.RunInFlight as exc:
        raise api_error(
            409, "run_in_flight", f"{payload.repository} already has a run in flight"
        ) from exc
    return success_envelope(run, count=1, total=1)


@router.get(
    "/tasks/agent-runs",
    response_model=Envelope[list[AgentRun]],
    summary="Agent runs, newest first",
    description=(
        "Narrowed by status and repository when given. The runs in flight "
        "are status=dispatched."
    ),
)
async def list_agent_runs(
    status: Annotated[str | None, Query(max_length=20)] = None,
    repository: Annotated[str | None, Query(max_length=100)] = None,
    principal: Principal = Depends(require_scope("tasks.agent-runs.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[AgentRun]]:
    """The runs that match, newest first."""
    runs = await agent_runs.list_runs(session, status=status, repository=repository)
    return success_envelope(runs, count=len(runs), total=len(runs))


@router.get(
    "/tasks/agent-runs/spend",
    response_model=Envelope[AgentSpend],
    summary="This month's agent runs, in total",
    description=(
        "The calendar month's runs and their known cost so far (UTC). "
        "task-manager-cog reads it before each dispatch and stops at its "
        "monthly cap."
    ),
)
async def agent_spend(
    principal: Principal = Depends(require_scope("tasks.agent-runs.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[AgentSpend]:
    """The month's runs and their cost so far."""
    spend = await agent_runs.month_spend(session)
    return success_envelope(spend, count=1, total=1)


def _ended_error(exc: Exception, run_id: str) -> Exception:
    if isinstance(exc, agent_runs.RunNotFound):
        return api_error(404, "run_not_found", f"No agent run {run_id}")
    return api_error(409, "run_ended", f"Agent run {run_id} has already ended")


@router.post(
    "/tasks/agent-runs/failures",
    response_model=Envelope[AgentRun],
    summary="Mark a dispatched agent run failed",
    description=(
        "For a run that could not be started, or that never reported. Its "
        "cost is unknown. A run that has already ended is a 409."
    ),
)
async def fail_agent_run(
    payload: AgentRunFailure,
    principal: Principal = Depends(require_scope("tasks.agent-runs.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[AgentRun]:
    """End one dispatched run as failed."""
    try:
        run = await agent_runs.fail_run(session, payload)
    except (agent_runs.RunNotFound, agent_runs.RunAlreadyEnded) as exc:
        raise _ended_error(exc, payload.run_id) from exc
    return success_envelope(run, count=1, total=1)


@router.post(
    "/tasks/agent-runs/results",
    response_model=Envelope[AgentResultAccepted],
    status_code=202,
    summary="Record how an agent run ended, and hand it to task-manager-cog",
    description=(
        "Posted once by the run's workflow. The result is recorded and "
        "queued for task-manager-cog, which moves the task. A second result "
        "for the same run is a 409, so a repeated post cannot count its cost "
        "twice."
    ),
)
async def submit_agent_result(
    payload: AgentRunResult,
    request: Request,
    principal: Principal = Depends(require_scope("tasks.agent-runs.report")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[AgentResultAccepted]:
    """Record the result and queue it; commit only once it is queued.

    A dropped result is a 502 and is not recorded, so the run stays in
    flight and the workflow's post can be made again.
    """
    settings = get_settings()
    try:
        run = await agent_runs.end_run(session, payload)
    except (agent_runs.RunNotFound, agent_runs.RunAlreadyEnded) as exc:
        raise _ended_error(exc, payload.run_id) from exc
    try:
        accepted = await agent_dispatch.dispatch_result(run, settings=settings)
    except agent_dispatch.DispatchError as exc:
        await session.rollback()
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The agent result was not enqueued: {exc}"
        ) from exc
    await session.commit()
    data = AgentResultAccepted(
        message_id=str(accepted.get("message_id") or ""), run_id=payload.run_id
    )
    return success_envelope(data, count=1, total=1)
