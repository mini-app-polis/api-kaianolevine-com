"""Asking task-manager-cog to put a task on the board, and its grooming records.

The callers of ``POST /v1/tasks`` are the fleet's sources of tasks. A source
hands over a draft of the work; task-manager-cog adds the board's template,
the id, tags and column, and creates the task. See
``mini_app_polis.api.contract.tasks``.

The ``/v1/tasks/groomings`` routes are task-manager-cog's memory between
sweeps of the board, and the month's paid grooming. See
``mini_app_polis.api.contract.groomings``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from identity.types import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import get_settings
from ..database import get_db_session
from ..schemas import (
    Envelope,
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
from ..services import groomings, job_queue, task_dispatch

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
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/tasks/groomings",
    response_model=Envelope[list[GroomingRecord]],
    summary="Every task's grooming record",
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
        version=get_settings().API_VERSION,
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
    record = await groomings.write_record(session, payload)
    return success_envelope(
        record, count=1, total=1, version=get_settings().API_VERSION
    )


@router.post(
    "/tasks/groomings/runs",
    response_model=Envelope[GroomingRunRecorded],
    status_code=201,
    summary="Record one paid grooming run",
)
async def record_grooming_run(
    payload: GroomingRunCreate,
    principal: Principal = Depends(require_scope("tasks.groomings.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[GroomingRunRecorded]:
    run = await groomings.record_run(session, payload)
    return success_envelope(run, count=1, total=1, version=get_settings().API_VERSION)


@router.get(
    "/tasks/groomings/spend",
    response_model=Envelope[GroomingSpend],
    summary="This month's paid grooming, in total",
)
async def grooming_spend(
    principal: Principal = Depends(require_scope("tasks.groomings.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[GroomingSpend]:
    spend = await groomings.month_spend(session)
    return success_envelope(spend, count=1, total=1, version=get_settings().API_VERSION)
