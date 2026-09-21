"""Asking deejay-cog to run.

The caller is watcher-cog, when a file lands in a watched Drive folder.
It used to call Prefect's ``create_flow_run`` directly; it now calls this,
and this enqueues onto deejay-jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from identity.types import Principal

from ..auth import require_scope
from ..config import get_settings
from ..schemas import (
    DeejayRunAccepted,
    DeejayRunRequest,
    Envelope,
    api_error,
    success_envelope,
)
from ..services import deejay_dispatch

router = APIRouter()


@router.post(
    "/deejay/runs",
    response_model=Envelope[DeejayRunAccepted],
    status_code=202,
    summary="Ask deejay-cog to run a flow",
    description=(
        "Enqueues one deejay-cog run in the given mode and acknowledges. "
        "The run happens afterwards, on the cog."
    ),
)
async def create_deejay_run(
    payload: DeejayRunRequest,
    principal: Principal = Depends(require_scope("deejay.runs.create")),
) -> Envelope[DeejayRunAccepted]:
    """Accept one deejay run and put it on the queue.

    The enqueue is awaited, not backgrounded: SQS acknowledges in
    milliseconds, so waiting makes a dropped job visible while the caller
    is still on the line. A failure is a 502 here *and* a message in the
    errors channel, because a Drive watcher will not act on the 502.
    """
    settings = get_settings()
    job = deejay_dispatch.DeejayJob(mode=payload.mode)

    try:
        accepted = await deejay_dispatch.dispatch_deejay(job, settings=settings)
    except deejay_dispatch.DispatchError as exc:
        raise api_error(
            502,
            "dispatch_failed",
            f"The deejay run was not enqueued: {exc}",
        ) from exc

    data = DeejayRunAccepted(
        message_id=str(accepted.get("message_id") or ""),
        mode=payload.mode,
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)
