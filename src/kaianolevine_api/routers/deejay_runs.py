"""Asking deejay-cog to run.

The caller is watcher-cog, on every tick that finds files in a watched
Drive folder, and an operator starting a sweep by hand. A deejay job is a
sweep of the folder, so the watcher names every file it sees and the API
claims each one (services/dispatch_claims.py): the sweep is enqueued when
any claim is new or renewed, and the request is deduplicated when every
file is already accounted for.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from identity.types import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import get_settings
from ..database import get_db_session
from ..schemas import (
    DeejayRunAccepted,
    DeejayRunRequest,
    Envelope,
    api_error,
    success_envelope,
)
from ..services import deejay_dispatch, dispatch_claims, job_queue

router = APIRouter()

#: The label drops and capped files are reported under.
_LABEL = "deejay dispatch"


@router.post(
    "/deejay/runs",
    response_model=Envelope[DeejayRunAccepted],
    status_code=202,
    summary="Ask deejay-cog to run a flow",
    description=(
        "Enqueues one deejay-cog run in the given mode and acknowledges. "
        "With drive_files, the files are claimed first and a request whose "
        "files are all already claimed is answered 200 with deduplicated "
        "true and the message id of an earlier sweep, and nothing is "
        "enqueued. The run happens afterwards, on the cog."
    ),
)
async def create_deejay_run(
    payload: DeejayRunRequest,
    response: Response,
    principal: Principal = Depends(require_scope("deejay.runs.create")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[DeejayRunAccepted]:
    """Accept one deejay run and put it on the queue.

    The enqueue is awaited, not backgrounded: SQS acknowledges in
    milliseconds, so waiting makes a dropped job visible while the caller
    is still on the line. A failure is a 502 here *and* a message in the
    errors channel, because a Drive watcher will not act on the 502 — and
    every claim this request took is released, so the next tick asks again.

    Without ``drive_files`` nothing is claimed and the sweep is always
    enqueued: that is an operator asking, and asking twice means twice.
    """
    settings = get_settings()
    job = deejay_dispatch.DeejayJob(mode=payload.mode)

    acquired: list[dispatch_claims.Claim] = []
    held: list[dispatch_claims.Claim] = []
    if payload.drive_files is not None:
        scope = dispatch_claims.scope_for("deejay", payload.mode)
        try:
            for file in payload.drive_files:
                claim = await dispatch_claims.claim(
                    session,
                    scope=scope,
                    drive_file_id=file.id,
                    revision=file.revision,
                )
                if claim.acquired:
                    acquired.append(claim)
                    continue
                held.append(claim)
                if claim.outcome is dispatch_claims.Outcome.CAPPED:
                    await job_queue.report_dropped(
                        dispatch_claims.capped_message(claim),
                        label=_LABEL,
                        heading="Drive file given up on",
                        settings=settings,
                    )
        except Exception:
            # A claim taken before the failure would otherwise hold its
            # file for the whole window with no sweep coming for it. The
            # rollback first: the session may be mid-way through the
            # statement that failed.
            await session.rollback()
            for claim in acquired:
                await dispatch_claims.release(session, claim)
            raise

        if not acquired:
            response.status_code = 200
            # The files may belong to more than one earlier sweep; any of
            # their ids is a job that covers this folder.
            earlier = next((c.message_id for c in held if c.message_id), "")
            data = DeejayRunAccepted(
                message_id=earlier, mode=payload.mode, deduplicated=True
            )
            return success_envelope(
                data, count=1, total=1, version=settings.API_VERSION
            )

    try:
        accepted = await deejay_dispatch.dispatch_deejay(job, settings=settings)
    except deejay_dispatch.DispatchError as exc:
        for claim in acquired:
            await dispatch_claims.release(session, claim)
        raise api_error(
            502,
            "dispatch_failed",
            f"The deejay run was not enqueued: {exc}",
        ) from exc

    message_id = str(accepted.get("message_id") or "")
    if acquired:
        await dispatch_claims.record(session, acquired, message_id)

    data = DeejayRunAccepted(message_id=message_id, mode=payload.mode)
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)
