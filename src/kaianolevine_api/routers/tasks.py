"""Asking task-manager-cog to put a task on the board, and its refining records.

The callers of ``POST /v1/tasks`` are the fleet's sources of tasks. A source
hands over a draft of the work; task-manager-cog adds the board's template,
the id, tags and column, and creates the task. See
``mini_app_polis.api.contract.tasks``. ``POST /v1/tasks/updates`` lets the
same caller comment on a task it asked for and move it to Review.

A machine may only ask for, and update, tasks under the sources it owns
(``task_sources`` in identity_registry.py). A task's external_id starts with
its source and a dot, so that is the whole check: the API keeps no record of
who asked for which task. A person who holds the scope is not limited.

The ``/v1/tasks/refinings`` routes are task-manager-cog's memory between
sweeps of the board, the month's paid refining, the hand-offs between
task-manager-cog and task-refiner-cog (services/refining_dispatch.py), the
claims task-refiner-cog takes on a job before refining it, and the runs it
reports the spent budget stopped. See
``mini_app_polis.api.contract.refinings``.

The ``/v1/tasks/agent-runs`` routes record the agent runs task-manager-cog
dispatches for Ready tasks, their month's spend, and each run's result,
which the run's workflow posts and this API hands to task-manager-cog
(services/agent_dispatch.py). See ``mini_app_polis.api.contract.agent_runs``.

``POST /v1/tasks/sweeps`` is the admin's, and the claude-session machine's:
it puts a sweep of the board on task-manager-cog's queue now, rather than
waiting for the hourly schedule. Its optional body can make that sweep the
one for refining, for agent dispatch, or both, whatever the time of day.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request
from identity.types import Principal
from mini_app_polis.api.contract import RETURN_RATE_DEFAULT_DAYS, RETURN_RATE_MAX_DAYS
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import get_settings
from ..database import get_db_session
from ..identity_registry import task_sources_for
from ..schemas import (
    AgentResultAccepted,
    AgentRun,
    AgentRunCreate,
    AgentRunFailure,
    AgentRunResult,
    AgentSpend,
    Envelope,
    RefiningBudgetSpentMarked,
    RefiningBudgetSpentRequest,
    RefiningClaimReleased,
    RefiningClaimRequest,
    RefiningClaimResult,
    RefiningJobAccepted,
    RefiningJobRequest,
    RefiningProposal,
    RefiningProposalAccepted,
    RefiningRecord,
    RefiningRecordWrite,
    RefiningReturnRate,
    RefiningRunCreate,
    RefiningRunRecorded,
    RefiningSpend,
    TaskCreateAccepted,
    TaskCreateRequest,
    TaskSweepAccepted,
    TaskSweepRequest,
    TaskUpdateAccepted,
    TaskUpdateRequest,
    api_error,
    success_envelope,
)
from ..services import (
    agent_dispatch,
    agent_runs,
    job_queue,
    refining_dispatch,
    refinings,
    task_dispatch,
)

router = APIRouter()

#: The one column a machine may move its task to: the work is done, for the
#: owner to check. Leaving ``section`` out only comments.
UPDATABLE_SECTIONS = frozenset({"Review"})


def _require_owned(principal: Principal, external_id: str) -> None:
    """403 unless a machine owns the source ``external_id`` starts with."""
    if principal.kind != "machine":
        return
    owned = task_sources_for(principal.subject)
    if not any(external_id.startswith(f"{source}.") for source in owned):
        raise api_error(
            403,
            "forbidden",
            f"{principal.subject} may only change tasks under its own sources"
            f" ({', '.join(sorted(owned)) or 'none'}).",
        )


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
    _require_owned(principal, payload.external_id)
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


@router.post(
    "/tasks/updates",
    response_model=Envelope[TaskUpdateAccepted],
    status_code=202,
    summary="Ask task-manager-cog to comment on a task and move it to Review",
    description=(
        "Enqueues one task update and acknowledges. The task is changed "
        "afterwards, by task-manager-cog, which reports a key that names no "
        "task. For the sources that ask for tasks: a machine may only update "
        "tasks under its own sources (403 otherwise). section may only be "
        "Review, or left out to only comment."
    ),
)
async def update_task_request(
    payload: TaskUpdateRequest,
    request: Request,
    principal: Principal = Depends(require_scope("tasks.requests.update")),
) -> Envelope[TaskUpdateAccepted]:
    """Accept one update to a task the caller asked for, and put it on the queue."""
    _require_owned(principal, payload.external_id)
    if payload.section is not None and payload.section not in UPDATABLE_SECTIONS:
        raise api_error(
            422,
            "validation_error",
            f"section may only be {', '.join(sorted(UPDATABLE_SECTIONS))}, or left out.",
        )
    settings = get_settings()
    try:
        accepted = await task_dispatch.dispatch_task_update(payload, settings=settings)
    except task_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The task update was not enqueued: {exc}"
        ) from exc

    data = TaskUpdateAccepted(
        message_id=str(accepted.get("message_id") or ""),
        external_id=payload.external_id,
    )
    return success_envelope(data, count=1, total=1)


@router.post(
    "/tasks/sweeps",
    response_model=Envelope[TaskSweepAccepted],
    status_code=202,
    summary="Ask task-manager-cog for a sweep of the board now",
    description=(
        "Enqueues one sweep on task-manager-cog's queue, the message its "
        "hourly schedule sends, and acknowledges. The sweep runs afterwards, "
        "in line with the queue's other jobs. Each request is its own sweep: "
        "asking twice runs two, one after the other. The body is optional: "
        "`refine` makes the sweep send tasks for refining and `agents` makes "
        "it dispatch agents, whatever the time of day. Every on/off setting "
        "and spending cap still applies."
    ),
)
async def request_sweep(
    request: Request,
    payload: TaskSweepRequest | None = Body(default=None),
    principal: Principal = Depends(require_scope("tasks.sweeps.create")),
) -> Envelope[TaskSweepAccepted]:
    """Put one sweep on the task manager's queue; a dropped one is a 502."""
    settings = get_settings()
    flags = payload or TaskSweepRequest()
    try:
        accepted = await task_dispatch.dispatch_sweep(
            settings=settings, refine=flags.refine, agents=flags.agents
        )
    except task_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The sweep was not enqueued: {exc}"
        ) from exc
    data = TaskSweepAccepted(message_id=str(accepted.get("message_id") or ""))
    return success_envelope(data, count=1, total=1)


@router.get(
    "/tasks/refinings",
    response_model=Envelope[list[RefiningRecord]],
    summary="Every task's refining record",
    description=(
        "What the task manager last made of each task: the fingerprint of the "
        "content it judged, the verdict and its problems, and whether it moved "
        "the task to Ready."
    ),
)
async def list_refinings(
    principal: Principal = Depends(require_scope("tasks.refinings.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[RefiningRecord]]:
    """All records at once: the board is small, and a sweep needs every one."""
    records = await refinings.list_records(session)
    return success_envelope(
        records,
        count=len(records),
        total=len(records),
    )


@router.post(
    "/tasks/refinings",
    response_model=Envelope[RefiningRecord],
    summary="Write one task's refining record",
    description="Keyed by task_gid: writing a task's record again replaces it.",
)
async def record_refining(
    payload: RefiningRecordWrite,
    principal: Principal = Depends(require_scope("tasks.refinings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[RefiningRecord]:
    """Replace one task's record with what the sweep made of it now."""
    record = await refinings.write_record(session, payload)
    return success_envelope(record, count=1, total=1)


@router.post(
    "/tasks/refinings/runs",
    response_model=Envelope[RefiningRunRecorded],
    status_code=201,
    summary="Record one paid refining run",
    description=(
        "One model run's tokens and cost, recorded so the month's spend can "
        "be capped. Recorded whether or not the run produced a proposal."
    ),
)
async def record_refining_run(
    payload: RefiningRunCreate,
    principal: Principal = Depends(require_scope("tasks.refinings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[RefiningRunRecorded]:
    """Record one paid run, for the month's spend."""
    run = await refinings.record_run(session, payload)
    return success_envelope(run, count=1, total=1)


@router.get(
    "/tasks/refinings/spend",
    response_model=Envelope[RefiningSpend],
    summary="This month's paid refining, in total",
    description=(
        "The calendar month's paid runs and their cost so far (UTC). The "
        "refiner reads it before each run and stops at its monthly cap."
    ),
)
async def refining_spend(
    principal: Principal = Depends(require_scope("tasks.refinings.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[RefiningSpend]:
    """The month's paid runs and their cost so far."""
    spend = await refinings.month_spend(session)
    return success_envelope(spend, count=1, total=1)


@router.get(
    "/tasks/refinings/return-rate",
    response_model=Envelope[RefiningReturnRate],
    summary="How often a task moved to Ready came back out",
    description=(
        "Over the last `days` (default 30): the tasks moved to Ready, the "
        "tasks sent back out of Ready, and returned ÷ moved (null when "
        "nothing moved). Counted from when these events began to be recorded "
        "when that is later than the window's start: `counting_since` says "
        "where the counts start."
    ),
)
async def refining_return_rate(
    days: Annotated[
        int, Query(ge=1, le=RETURN_RATE_MAX_DAYS)
    ] = RETURN_RATE_DEFAULT_DAYS,
    principal: Principal = Depends(require_scope("tasks.refinings.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[RefiningReturnRate]:
    """Moves to Ready, returns from it, and their ratio, over a window."""
    rate = await refinings.return_rate(session, days)
    return success_envelope(rate, count=1, total=1)


@router.post(
    "/tasks/refinings/jobs",
    response_model=Envelope[RefiningJobAccepted],
    status_code=202,
    summary="Ask task-refiner-cog to refine one task",
    description=(
        "Enqueues one refining job and acknowledges. The refiner's proposal "
        "comes back through POST /v1/tasks/refinings/proposals."
    ),
)
async def request_refining(
    payload: RefiningJobRequest,
    request: Request,
    principal: Principal = Depends(require_scope("tasks.refinings.write")),
) -> Envelope[RefiningJobAccepted]:
    """Put one job on the refiner's queue; a dropped job is a 502."""
    settings = get_settings()
    try:
        accepted = await refining_dispatch.dispatch_job(payload, settings=settings)
    except refining_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The refining job was not enqueued: {exc}"
        ) from exc
    data = RefiningJobAccepted(
        message_id=str(accepted.get("message_id") or ""),
        task_gid=payload.task.task_gid,
    )
    return success_envelope(data, count=1, total=1)


@router.post(
    "/tasks/refinings/claims",
    response_model=Envelope[RefiningClaimResult],
    summary="Claim one refining job before refining it",
    description=(
        "Keyed on the task and the fingerprint of the content the job was sent "
        "for. claimed is true for the first caller and false while the claim "
        "is held, so a job delivered twice is refined once. A claim given back "
        "through POST /v1/tasks/refinings/claims/release can be taken again."
    ),
)
async def claim_refining(
    payload: RefiningClaimRequest,
    principal: Principal = Depends(require_scope("tasks.refinings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[RefiningClaimResult]:
    """Claim the job; a claim already held is an answer, not an error."""
    result = await refinings.claim(session, payload)
    return success_envelope(result, count=1, total=1)


@router.post(
    "/tasks/refinings/claims/release",
    response_model=Envelope[RefiningClaimReleased],
    summary="Give a refining claim back after a failed run",
    description=(
        "Marks a held claim released, so the next send of the same content can "
        "claim it. released is false when no claim was held."
    ),
)
async def release_refining_claim(
    payload: RefiningClaimRequest,
    principal: Principal = Depends(require_scope("tasks.refinings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[RefiningClaimReleased]:
    """Release the claim, if one is held."""
    result = await refinings.release_claim(session, payload)
    return success_envelope(result, count=1, total=1)


@router.post(
    "/tasks/refinings/budget-spent",
    response_model=Envelope[RefiningBudgetSpentMarked],
    summary="Report a refining run the spent budget stopped",
    description=(
        "Keyed on the task, holding the fingerprint of the content the job was "
        "sent for; a later stop for the task replaces it. Served on each "
        "record as budget_spent_fingerprint, and cleared when a claim for the "
        "same task and content is won. Marking needs no record for the task."
    ),
)
async def mark_refining_budget_spent(
    payload: RefiningBudgetSpentRequest,
    principal: Principal = Depends(require_scope("tasks.refinings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[RefiningBudgetSpentMarked]:
    """Store the stop, so the sweep sends the same content again."""
    result = await refinings.mark_budget_spent(session, payload)
    return success_envelope(result, count=1, total=1)


@router.post(
    "/tasks/refinings/proposals",
    response_model=Envelope[RefiningProposalAccepted],
    status_code=202,
    summary="Hand a refiner's proposal to task-manager-cog",
    description=(
        "Enqueues the proposal on task-manager-cog's queue, which applies it "
        "to the board. The refiner itself never writes to the board."
    ),
)
async def submit_refining_proposal(
    payload: RefiningProposal,
    request: Request,
    principal: Principal = Depends(require_scope("tasks.refinings.write")),
) -> Envelope[RefiningProposalAccepted]:
    """Put one proposal on the task manager's queue; a dropped one is a 502."""
    settings = get_settings()
    try:
        accepted = await refining_dispatch.dispatch_proposal(payload, settings=settings)
    except refining_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The refining proposal was not enqueued: {exc}"
        ) from exc
    data = RefiningProposalAccepted(
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
