"""The record of secret rotations.

rotator-cog writes one event per rotation of one secret in one Doppler
config, and reads the latest event per secret and config in its daily sweep
to decide what is due. The owner's hand-run rotations write the same events
under his own identity. No event carries a secret value. See
``mini_app_polis.api.contract.secret_rotations``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..database import get_db_session
from ..schemas import (
    Envelope,
    RotationConfig,
    RotationEvent,
    RotationEventCreate,
    success_envelope,
)
from ..services import rotation_events

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
