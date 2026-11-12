"""Asking deejay-cog to run.

The caller is watcher-cog, on every tick that finds files in a watched
Drive folder, and an operator starting a sweep by hand. A deejay job is a
sweep of the folder, so the watcher names every file it sees and the API
claims them all at once (services/dispatch_claims.py), at the same cost for
five files as for five hundred: the sweep is enqueued when
any claim is new or renewed, and the request is deduplicated when every
file is already accounted for.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
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
        "enqueued. An identical request within about a minute is collapsed "
        "by the queue and answered the same way; one later is queued. The "
        "run happens afterwards, on the cog."
    ),
)
async def create_deejay_run(
    payload: DeejayRunRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_scope("deejay.runs.create")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[DeejayRunAccepted]:
    """Accept one deejay run and put it on the queue.

    The enqueue is awaited, not backgrounded: SQS acknowledges in
    milliseconds, so waiting makes a dropped job visible while the caller
    is still on the line. A failure is a 502 here *and* one message in the
    errors channel (its fault report, naming the drop), because a Drive
    watcher will not act on the 502 — and
    every claim this request took is released, so the next tick asks again.

    Without ``drive_files`` nothing is claimed and the sweep is enqueued:
    that is an operator asking, and asking twice means twice — unless the
    second is within the queue's collapse window
    (:data:`job_queue.DEDUP_WINDOW_SECONDS`), when it is the same request
    sent again and is answered with the first's message id.
    """
    settings = get_settings()
    job = deejay_dispatch.DeejayJob(mode=payload.mode)

    acquired: list[dispatch_claims.Claim] = []
    held: list[dispatch_claims.Claim] = []
    if payload.drive_files is not None:
        scope = dispatch_claims.scope_for("deejay", payload.mode)
        try:
            claims = await dispatch_claims.claim_many(
                session,
                scope=scope,
                files=[(file.id, file.revision) for file in payload.drive_files],
            )
            acquired = [c for c in claims if c.acquired]
            held = [c for c in claims if not c.acquired]
            for claim in held:
                if claim.outcome is dispatch_claims.Outcome.CAPPED:
                    await job_queue.report_dropped(
                        dispatch_claims.capped_message(claim),
                        label=_LABEL,
                        heading="Drive file given up on",
                        settings=settings,
                    )
        except Exception:
            # The claims are taken together, so a failure while taking them
            # leaves none. One that comes after (a report) would otherwise
            # leave the files held for the whole window with no sweep coming
            # for them. The rollback first: the session may be mid-way
            # through the statement that failed.
            await session.rollback()
            await dispatch_claims.release_all(session, acquired)
            raise

        if not acquired:
            response.status_code = 200
            # The files may belong to more than one earlier sweep; any of
            # their ids is a job that covers this folder.
            earlier = next((c.message_id for c in held if c.message_id), "")
            data = DeejayRunAccepted(
                message_id=earlier, mode=payload.mode, deduplicated=True
            )
            return success_envelope(data, count=1, total=1)

    try:
        accepted = await deejay_dispatch.dispatch_deejay(
            job, settings=settings, caller=principal.display_name or principal.subject
        )
    except deejay_dispatch.DispatchError as exc:
        await dispatch_claims.release_all(session, acquired)
        job_queue.record_drop(request, exc)
        raise api_error(
            502,
            "dispatch_failed",
            f"The deejay run was not enqueued: {exc}",
        ) from exc

    message_id = str(accepted.get("message_id") or "")
    if acquired:
        await dispatch_claims.record(session, acquired, message_id)

    deduplicated = bool(accepted.get("deduplicated"))
    if deduplicated:
        response.status_code = 200
    data = DeejayRunAccepted(
        message_id=message_id, mode=payload.mode, deduplicated=deduplicated
    )
    return success_envelope(data, count=1, total=1)
