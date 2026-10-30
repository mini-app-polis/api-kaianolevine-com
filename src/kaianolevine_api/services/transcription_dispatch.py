"""Handing transcript and voice-note work to transcription-cog.

watcher-cog asks for a run with a ``mode``, which becomes this message's
payload unchanged.

**A job is one file, not a folder sweep.** deejay-cog's job is a sweep, but
transcription-cog's work does not fit one: a single transcript's extraction
can take five minutes, and a sweep of five would outlive the Lambda's
900-second ceiling. watcher-cog already knows which files changed, so it
names them, one request per file, and each file gets an invocation to
itself. The one exception is ``voicenotes-cleanup``, the retention sweep
over the archive, which names no file.

``wcs-note`` is the other: a note written on the site, which
carries its own text instead of naming a file. The API produces it itself,
from ``POST /v1/wcs/wiki/admin/notes``; watcher-cog never asks for it.

Two requests for one file produce two jobs. That is safe: the cog skips a
file that is gone from its inbox, and runs one job at a time — which is
the consumer's concurrency setting to provide, not something the message
can express.

The mechanics of sending, and of insisting that a send landed, are shared
with every dispatcher; see :mod:`.job_queue`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mini_app_polis.api.contract import (
    TranscriptionRunMessage,
    TranscriptionRunPayload,
)

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "DispatchError",
    "TranscriptionJob",
    "dispatch_transcription",
]


@dataclass(frozen=True)
class TranscriptionJob:
    """One run of transcription-cog: one file in one mode, the cleanup sweep,
    or one note written on the site."""

    mode: str
    drive_file_id: str | None = None
    #: ``wcs-note``: the note, with ``id``, ``date``, ``instructor``,
    #: ``title`` and ``text``.
    note: dict[str, str] | None = None

    def as_message(self) -> dict[str, Any]:
        """Render this job as one ``transcription.run`` queue message."""
        payload: dict[str, Any] = {"mode": self.mode}
        if self.drive_file_id:
            payload["drive_file_id"] = self.drive_file_id
        if self.note is not None:
            payload["note"] = self.note
        return TranscriptionRunMessage(
            payload=TranscriptionRunPayload.model_validate(payload)
        ).model_dump(mode="json", exclude_none=True)

    def describe(self) -> str:
        """How this job reads in a log line or a drop report."""
        if self.drive_file_id:
            return f"transcription {self.mode} file {self.drive_file_id}"
        if self.note is not None:
            return f"transcription {self.mode} {self.note.get('id', '')}".rstrip()
        return f"transcription {self.mode}"


async def dispatch_transcription(job: TranscriptionJob, *, settings: Settings) -> dict:
    """Enqueue one transcription job. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        job.as_message(),
        job.describe(),
        cog="transcription",
        label="transcription dispatch",
        # The caller is a Drive watcher that will not read the 502, so the
        # route's fault report says this. A dropped transcription job is a
        # transcript or a voice note sitting in Drive while nothing says it
        # has not been processed.
        heading="Transcription run not dispatched",
        settings=settings,
    )
