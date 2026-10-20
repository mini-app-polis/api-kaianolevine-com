"""Asking task-manager-cog to put a task on the board.

The callers are the fleet's sources of tasks. A source hands over a draft of
the work; task-manager-cog adds the board's template, the id, tags and
column, and creates the task. See ``mini_app_polis.api.contract.tasks``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from identity.types import Principal

from ..auth import require_scope
from ..config import get_settings
from ..schemas import (
    Envelope,
    TaskCreateAccepted,
    TaskCreateRequest,
    api_error,
    success_envelope,
)
from ..services import job_queue, task_dispatch

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
