"""Handing transcript and voice-note work to transcription-cog.

This replaces watcher-cog calling Prefect's ``create_flow_run`` on the
``transcription-cog`` router deployment. The ``mode`` it passed carries
over unchanged.

**A job is one file, not a folder sweep.** deejay-cog's job is a sweep, but
transcription-cog's work does not fit one: a single transcript's extraction
can take five minutes, and a sweep of five would outlive the Lambda's
900-second ceiling. watcher-cog already knows which files changed, so it
names them, one request per file, and each file gets an invocation to
itself. The one exception is ``voicenotes-cleanup``, the retention sweep
over the archive, which names no file.

Two requests for one file produce two jobs. That is safe: the cog skips a
file that is no longer in its inbox, and runs one job at a time — which is
the consumer's concurrency setting to provide, not something the message
can express.

The mechanics of sending, and of insisting that a send landed, are shared
with every dispatcher; see :mod:`.job_queue`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "MESSAGE_VERSION",
    "TYPE_RUN",
    "DispatchError",
    "TranscriptionJob",
    "dispatch_transcription",
]

#: Schema version carried on every message. The consumer refuses a version
#: it does not speak rather than misreading it, which is what makes a
#: producer/consumer redeploy safe.
MESSAGE_VERSION = 1

#: The one message type on transcription-jobs. It is a producer-bug
#: detector, not a router: the queue is transcription's own, so an
#: unrecognised type there is something enqueued wrongly. ``mode`` in the
#: payload is what routes.
TYPE_RUN = "transcription.run"


@dataclass(frozen=True)
class TranscriptionJob:
    """One run of transcription-cog: one file in one mode, or the cleanup sweep."""

    mode: str
    drive_file_id: str | None = None

    def as_message(self) -> dict[str, Any]:
        """Render this job as one ``TYPE_RUN`` queue message."""
        payload: dict[str, Any] = {"mode": self.mode}
        if self.drive_file_id:
            payload["drive_file_id"] = self.drive_file_id
        return {
            "type": TYPE_RUN,
            "version": MESSAGE_VERSION,
            "payload": payload,
        }

    def describe(self) -> str:
        """How this job reads in a log line or a drop report."""
        if self.drive_file_id:
            return f"transcription {self.mode} file {self.drive_file_id}"
        return f"transcription {self.mode}"


async def dispatch_transcription(job: TranscriptionJob, *, settings: Settings) -> dict:
    """Enqueue one transcription job. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        job.as_message(),
        job.describe(),
        cog="transcription",
        label="transcription dispatch",
        report=lambda text: _report(text, settings),
        settings=settings,
    )


async def _report(message: str, settings: Settings) -> None:
    """Say a job was dropped, in the one place someone is watching.

    The caller is a Drive watcher that will not read the 502. A dropped
    transcription job is a transcript or a voice note sitting in Drive while
    nothing says it has not been processed.
    """
    await job_queue.report_dropped(
        message,
        label="transcription dispatch",
        heading="Transcription run not dispatched",
        settings=settings,
    )
