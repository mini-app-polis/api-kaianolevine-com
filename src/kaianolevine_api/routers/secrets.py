"""The record of secret rotations, and asking for one.

rotator-cog writes one event per rotation of one secret in one Doppler
config, and reads the latest event per secret and config in its daily sweep
to decide what is due. The owner's hand-run rotations write the same events
under his own identity. The owner can also ask the rotator for a rotation
now (services/rotation_dispatch.py), under a scope of its own: holding the
record's scopes does not let a caller start one. No event or request carries
a secret value. See ``mini_app_polis.api.contract.secret_rotations``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from identity.types import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import get_settings
from ..database import get_db_session
from ..schemas import (
    Envelope,
    RotationConfig,
    RotationEvent,
    RotationEventCreate,
    RotationRequest,
    RotationRequestAccepted,
    api_error,
    success_envelope,
)
from ..services import job_queue, rotation_dispatch, rotation_events

router = APIRouter()


@router.post(
    "/secrets/rotation-events",
    response_model=Envelope[RotationEvent],
    summary="Record one rotation of a secret, or update it",
    description=(
        "Keyed on name, config and minted: writing the same key again "
        "updates that event, as when its old value is retired. Events are "
        "never deleted. No field carries a secret value."
    ),
)
async def record_rotation_event(
    payload: RotationEventCreate,
    principal: Principal = Depends(require_scope("secrets.rotation-events.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[RotationEvent]:
    """Write one event, or update the one with the same key."""
    event = await rotation_events.record_event(session, payload)
    return success_envelope(event, count=1, total=1)


@router.get(
    "/secrets/rotation-events",
    response_model=Envelope[list[RotationEvent]],
    summary="Rotation events, newest minted first",
    description=(
        "Narrowed by name and config when given. latest=true keeps only the "
        "newest event for each name and config, which is what the daily "
        "sweep reads. meta.total counts every match, across pages."
    ),
)
async def list_rotation_events(
    name: Annotated[str | None, Query(max_length=128)] = None,
    config: Annotated[RotationConfig | None, Query()] = None,
    latest: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(require_scope("secrets.rotation-events.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[RotationEvent]]:
    """The events that match, newest minted first.

    The default page is the largest, so the sweep's unpaged read of the
    latest events sees every secret.
    """
    events, total = await rotation_events.list_events(
        session,
        name=name,
        config=config,
        latest=latest,
        limit=limit,
        offset=offset,
    )
    return success_envelope(events, count=len(events), total=total)


@router.post(
    "/secrets/rotations",
    response_model=Envelope[RotationRequestAccepted],
    status_code=202,
    summary="Ask rotator-cog to rotate one secret now",
    description=(
        "Enqueues one rotation on the rotator's queue and acknowledges. The "
        "rotation runs afterwards and is recorded as a rotation event. "
        "compromised retires the old value at once. Ask the production API, "
        "with config set to the Doppler config to rotate: only production "
        "has a rotator queue."
    ),
)
async def request_rotation(
    payload: RotationRequest,
    request: Request,
    principal: Principal = Depends(require_scope("secrets.rotations.create")),
) -> Envelope[RotationRequestAccepted]:
    """Accept one rotation request and put it on the rotator's queue.

    The enqueue is awaited, so a dropped request is a 502 while the caller
    is still on the line, and one message in the errors channel.
    """
    settings = get_settings()
    try:
        accepted = await rotation_dispatch.dispatch_rotation(payload, settings=settings)
    except rotation_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(
            502, "dispatch_failed", f"The rotation was not enqueued: {exc}"
        ) from exc
    data = RotationRequestAccepted(
        message_id=str(accepted.get("message_id") or ""),
        name=payload.name,
        config=payload.config,
    )
    return success_envelope(data, count=1, total=1)
