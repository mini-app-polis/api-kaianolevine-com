"""Handing DJ-set work to deejay-cog.

This replaces watcher-cog calling Prefect's ``create_flow_run`` on the
``deejay-cog`` deployment. The parameters it passed — ``{"mode": ...}`` —
become this message's payload unchanged, so watcher's folder-to-mode map
is the routing table as it stands, and the cog's ``deejay_router(mode)``
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

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "MESSAGE_VERSION",
    "TYPE_RUN",
    "DeejayJob",
    "DispatchError",
    "dispatch_deejay",
]

#: Schema version carried on every message. The consumer refuses a version
#: it does not speak rather than misreading it, which is what makes a
#: producer/consumer redeploy safe.
MESSAGE_VERSION = 1

#: The one message type on deejay-jobs. It is a producer-bug detector, not
#: a router: the queue is deejay's own, so an unrecognised type there is
#: something enqueued wrongly. ``mode`` in the payload is what routes.
TYPE_RUN = "deejay.run"


@dataclass(frozen=True)
class DeejayJob:
    """One run of deejay-cog's router, in one mode."""

    mode: str

    def as_message(self) -> dict[str, Any]:
        """Render this job as one ``TYPE_RUN`` queue message."""
        return {
            "type": TYPE_RUN,
            "version": MESSAGE_VERSION,
            "payload": {"mode": self.mode},
        }


async def dispatch_deejay(job: DeejayJob, *, settings: Settings) -> dict:
    """Enqueue one deejay run. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        job.as_message(),
        f"deejay {job.mode}",
        queue_url=settings.DEEJAY_QUEUE_URL,
        url_setting="DEEJAY_QUEUE_URL",
        label="deejay dispatch",
        report=lambda text: _report(text, settings),
        settings=settings,
    )


async def _report(message: str, settings: Settings) -> None:
    """Say a job was dropped, in the one place someone is watching.

    The caller is a Drive watcher that will not read the 502. A dropped
    deejay job is a DJ set that never reaches the catalog while the folder
    it was uploaded to looks processed from the outside.
    """
    await job_queue.report_dropped(
        message,
        label="deejay dispatch",
        heading="Deejay run not dispatched",
        settings=settings,
    )
