"""Handing DJ-set work to deejay-cog.

watcher-cog asks for a run with ``{"mode": ...}``, and that becomes this
message's payload unchanged, so watcher's folder-to-mode map is the
routing table as it stands, and the cog's ``deejay_router(mode)``
reads the same thing it always did.

A deejay job is a folder sweep, not a file: ``process-new-files`` lists
the source folder and works through whatever it finds. The message
therefore carries only the mode. Two triggers for one upload produce two
sweeps; run one after the other, the second finds the files already
archived. Run at the same time, they race over the same files — which is
the consumer's concurrency setting to prevent, not something the message
can express.

The mechanics of sending, and of insisting that a send landed, are shared
with every dispatcher; see :mod:`.job_queue`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mini_app_polis.api.contract import DeejayRunMessage, DeejayRunPayload

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "DeejayJob",
    "DispatchError",
    "dispatch_deejay",
]


@dataclass(frozen=True)
class DeejayJob:
    """One run of deejay-cog's router, in one mode."""

    mode: str

    def as_message(self) -> dict[str, Any]:
        """Render this job as one ``deejay.run`` queue message."""
        return DeejayRunMessage(payload=DeejayRunPayload(mode=self.mode)).model_dump(
            mode="json", exclude_none=True
        )


async def dispatch_deejay(job: DeejayJob, *, settings: Settings) -> dict:
    """Enqueue one deejay run. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        job.as_message(),
        f"deejay {job.mode}",
        cog="deejay",
        label="deejay dispatch",
        # The caller is a Drive watcher that will not read the 502, so the
        # route's fault report says this. A dropped deejay job is a DJ set
        # that never reaches the catalog while the folder it was uploaded to
        # looks processed from the outside.
        heading="Deejay run not dispatched",
        settings=settings,
    )
