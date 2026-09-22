"""Asking transcription-cog to run.

The caller is watcher-cog, once for each file that lands in a watched
Drive folder. It used to call Prefect's ``create_flow_run`` for the whole
folder; it now calls this per file, and this enqueues onto
transcription-jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from identity.types import Principal

from ..auth import require_scope
from ..config import get_settings
from ..schemas import (
    Envelope,
    TranscriptionRunAccepted,
    TranscriptionRunRequest,
    api_error,
    success_envelope,
)
from ..services import transcription_dispatch

router = APIRouter()


@router.post(
    "/transcription/runs",
    response_model=Envelope[TranscriptionRunAccepted],
    status_code=202,
    summary="Ask transcription-cog to process one file",
    description=(
        "Enqueues one transcription-cog job and acknowledges: one Drive file "
        "in the given mode, or the voicenotes retention sweep, which names "
        "no file. The run happens afterwards, on the cog."
    ),
)
async def create_transcription_run(
    payload: TranscriptionRunRequest,
    principal: Principal = Depends(require_scope("transcription.runs.create")),
) -> Envelope[TranscriptionRunAccepted]:
    """Accept one transcription job and put it on the queue.

    The enqueue is awaited, not backgrounded: SQS acknowledges in
    milliseconds, so waiting makes a dropped job visible while the caller
    is still on the line. A failure is a 502 here *and* a message in the
    errors channel, because a Drive watcher will not act on the 502.
    """
    settings = get_settings()
    job = transcription_dispatch.TranscriptionJob(
        mode=payload.mode, drive_file_id=payload.drive_file_id
    )

    try:
        accepted = await transcription_dispatch.dispatch_transcription(
            job, settings=settings
        )
    except transcription_dispatch.DispatchError as exc:
        raise api_error(
            502,
            "dispatch_failed",
            f"The transcription run was not enqueued: {exc}",
        ) from exc

    data = TranscriptionRunAccepted(
        message_id=str(accepted.get("message_id") or ""),
        mode=payload.mode,
        drive_file_id=payload.drive_file_id,
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)
