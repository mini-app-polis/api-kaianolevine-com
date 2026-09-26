"""Asking transcription-cog to run.

The caller is watcher-cog, once for each file sitting in a watched Drive
folder, and it asks again on every tick until the cog moves the file out.
Each file is claimed before it is enqueued (services/dispatch_claims.py),
so the repeats are answered as deduplicated rather than becoming jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from identity.types import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import get_settings
from ..database import get_db_session
from ..schemas import (
    Envelope,
    TranscriptionRunAccepted,
    TranscriptionRunRequest,
    api_error,
    success_envelope,
)
from ..services import dispatch_claims, job_queue, transcription_dispatch

router = APIRouter()

#: The label drops and capped files are reported under.
_LABEL = "transcription dispatch"


@router.post(
    "/transcription/runs",
    response_model=Envelope[TranscriptionRunAccepted],
    status_code=202,
    summary="Ask transcription-cog to process one file",
    description=(
        "Enqueues one transcription-cog job and acknowledges: one Drive file "
        "in the given mode, or the voicenotes retention sweep, which names "
        "no file. A file already claimed by an earlier request is answered "
        "200 with deduplicated true and the message id of the job that has "
        "it, and nothing is enqueued. The run happens afterwards, on the cog."
    ),
)
async def create_transcription_run(
    payload: TranscriptionRunRequest,
    response: Response,
    principal: Principal = Depends(require_scope("transcription.runs.create")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[TranscriptionRunAccepted]:
    """Accept one transcription job and put it on the queue, once per file.

    The enqueue is awaited, not backgrounded: SQS acknowledges in
    milliseconds, so waiting makes a dropped job visible while the caller
    is still on the line. A failure is a 502 here *and* a message in the
    errors channel, because a Drive watcher will not act on the 502 — and
    it releases the claim, so the next tick asks again instead of the file
    waiting out the window with nothing running for it.

    The retention sweep names no file and is never claimed: it is an
    operator's request, and asking for it twice means wanting it twice.
    """
    settings = get_settings()
    job = transcription_dispatch.TranscriptionJob(
        mode=payload.mode, drive_file_id=payload.drive_file_id
    )

    claim: dispatch_claims.Claim | None = None
    if payload.drive_file_id:
        claim = await dispatch_claims.claim(
            session,
            scope=dispatch_claims.scope_for("transcription", payload.mode),
            drive_file_id=payload.drive_file_id,
        )
        if claim.outcome is dispatch_claims.Outcome.CAPPED:
            await job_queue.report_dropped(
                dispatch_claims.capped_message(claim),
                label=_LABEL,
                heading="Drive file given up on",
                settings=settings,
            )
        if not claim.acquired:
            response.status_code = 200
            data = TranscriptionRunAccepted(
                message_id=claim.message_id,
                mode=payload.mode,
                drive_file_id=payload.drive_file_id,
                deduplicated=True,
            )
            return success_envelope(
                data, count=1, total=1, version=settings.API_VERSION
            )

    try:
        accepted = await transcription_dispatch.dispatch_transcription(
            job, settings=settings
        )
    except transcription_dispatch.DispatchError as exc:
        if claim is not None:
            await dispatch_claims.release(session, claim)
        raise api_error(
            502,
            "dispatch_failed",
            f"The transcription run was not enqueued: {exc}",
        ) from exc

    message_id = str(accepted.get("message_id") or "")
    if claim is not None:
        await dispatch_claims.record(session, [claim], message_id)

    data = TranscriptionRunAccepted(
        message_id=message_id,
        mode=payload.mode,
        drive_file_id=payload.drive_file_id,
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)
