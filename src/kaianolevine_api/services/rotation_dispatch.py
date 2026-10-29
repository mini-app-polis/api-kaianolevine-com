"""Asking rotator-cog to rotate one secret now, or every secret at once.

The owner asks through ``POST /v1/secrets/rotations``; the request goes on
rotator-jobs.fifo as a ``rotation.job``, and the rotator records the
rotation as an event once it has run. An incident
(``POST /v1/secrets/rotation-incidents``) goes on the same queue, in the
same message group, as one ``rotation.incident``: the rotator expands it
into a job per secret. The daily sweep does not come through
here: it is sent by the rotator's own schedule.

Only production has a rotator queue. A development API derives
rotator-dev-jobs.fifo, which does not exist, so a request there fails as a
drop; a rotation of the dev config is asked for in production, with
``config`` set to dev.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.api.contract import (
    RotationIncidentMessage,
    RotationIncidentPayload,
    RotationJobMessage,
    RotationRequest,
)

from ..config import Settings
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "DispatchError",
    "dispatch_incident",
    "dispatch_rotation",
    "incident_message",
    "rotation_message",
]


def rotation_message(request: RotationRequest) -> dict[str, Any]:
    """Render one request as a ``rotation.job`` queue message."""
    return RotationJobMessage(payload=request).model_dump(mode="json")


async def dispatch_rotation(request: RotationRequest, *, settings: Settings) -> dict:
    """Enqueue one rotation for the rotator. Raises DispatchError.

    The queue's content-based deduplication makes the same request asked
    again within five minutes one job, so a double submit rotates once.
    """
    return await job_queue.enqueue(
        rotation_message(request),
        f"a {request.mode} rotation of {request.name} in {request.config}",
        cog="rotator",
        label="rotation",
        heading="Rotation not dispatched",
        settings=settings,
    )


def incident_message(incident_id: str) -> dict[str, Any]:
    """Render one incident as a ``rotation.incident`` queue message."""
    return RotationIncidentMessage(
        payload=RotationIncidentPayload(incident_id=incident_id)
    ).model_dump(mode="json")


async def dispatch_incident(incident_id: str, *, settings: Settings) -> dict:
    """Enqueue one incident for the rotator. Raises DispatchError.

    In the rotator's one message group, as every rotation job, so the jobs
    it expands into keep their order behind it.
    """
    return await job_queue.enqueue(
        incident_message(incident_id),
        f"rotation incident {incident_id}",
        cog="rotator",
        label="rotation incident",
        heading="Rotation incident not dispatched",
        settings=settings,
    )
