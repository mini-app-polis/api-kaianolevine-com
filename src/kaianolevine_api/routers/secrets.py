"""The record of secret rotations, and asking for one.

rotator-cog writes one event per rotation of one secret in one Doppler
config, and reads the latest event per secret and config in its daily sweep
to decide what is due. The owner's hand-run rotations write the same events
under his own identity. The owner can also ask the rotator for a rotation
now (services/rotation_dispatch.py), under a scope of its own: holding the
record's scopes does not let a caller start one. No event or request carries
a secret value. See ``mini_app_polis.api.contract.secret_rotations``.

Doppler reports which secret names changed in a config through its
``config.secrets.update`` webhook (``POST /v1/webhooks/doppler``, verified by
signature). The names are kept as changes the rotator's sweep compares with
the record, so a value changed outside the rotation process is found.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from identity.types import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import Settings, get_settings
from ..database import get_db_session
from ..schemas import (
    DopplerWebhookResult,
    Envelope,
    KeyUse,
    RotationConfig,
    RotationEvent,
    RotationEventCreate,
    RotationRequest,
    RotationRequestAccepted,
    SecretChange,
    api_error,
    success_envelope,
)
from ..services import (
    job_queue,
    key_use,
    rotation_dispatch,
    rotation_events,
    secret_changes,
)
from ..services.webhook_signature import verify_sha256_signature

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


@router.get(
    "/secrets/key-use",
    response_model=Envelope[KeyUse],
    summary="When a machine last used its previous key",
    description=(
        "From this API's audit trail: the newest request the machine proved "
        "with its previous key, or null. The rotator retires a previous key "
        "once this is a day old. Machine names only; no key is read."
    ),
)
async def read_key_use(
    machine: Annotated[
        str, Query(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9-]*$")
    ],
    principal: Principal = Depends(require_scope("secrets.key-use.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[KeyUse]:
    """The machine's last use of its previous key."""
    last = await key_use.last_previous_use(session, machine)
    return success_envelope(KeyUse(machine=machine, last_used=last), count=1, total=1)


@router.post(
    "/webhooks/doppler",
    response_model=Envelope[DopplerWebhookResult],
    summary="Doppler secrets.update webhook: which secret names changed",
    description=(
        "Receives Doppler's config.secrets.update webhook, verifies "
        "X-Doppler-Signature (HMAC-SHA256 over the raw body), and stores the "
        "names added, removed and updated in the prd or dev config of the "
        "recorded project. Doppler sends names, never values, and only names, "
        "the config and the time are kept. A retried delivery is stored once."
        " Intentionally public: Doppler's request signature gates it, not a bearer token."
    ),
    include_in_schema=False,
)
async def doppler_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[DopplerWebhookResult]:
    """Verify one Doppler delivery and store the names it lists."""
    raw_body = await request.body()
    secret = (settings.DOPPLER_WEBHOOK_SECRET or "").strip()
    if not secret:
        # Fail closed: an unsigned delivery is never stored.
        raise api_error(500, "config_error", "Doppler webhook secret is not configured")
    # The previous secret is accepted only beside a current one.
    previous = (settings.DOPPLER_WEBHOOK_SECRET_PREVIOUS or "").strip()
    if not verify_sha256_signature(
        secrets=[secret, previous] if previous else [secret],
        raw_body=raw_body,
        signature=request.headers.get("X-Doppler-Signature"),
    ):
        raise api_error(401, "unauthorized", "Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise api_error(400, "parse_error", "Body is not valid JSON") from None
    if not isinstance(payload, dict):
        raise api_error(400, "parse_error", "Body is not a JSON object")

    def ignored(reason: str) -> Envelope[DopplerWebhookResult]:
        # 200, not an error: Doppler should not retry a delivery this route
        # chose not to store.
        return success_envelope(
            DopplerWebhookResult(stored=0, reason=reason), count=1, total=1
        )

    if payload.get("type") != "config.secrets.update":
        return ignored("other_type")
    if secret_changes.project_of(payload) != settings.DOPPLER_WEBHOOK_PROJECT:
        return ignored("other_project")
    config = secret_changes.config_of(payload)
    if config is None:
        return ignored("other_config")

    stored = await secret_changes.record_delivery(
        session,
        raw_body=raw_body,
        config=config,
        changes=secret_changes.changed_names(payload),
    )
    if stored == 0:
        return ignored("retried")
    return success_envelope(DopplerWebhookResult(stored=stored), count=1, total=1)


@router.get(
    "/secrets/changes",
    response_model=Envelope[list[SecretChange]],
    summary="Secret names Doppler reported changed, oldest first",
    description=(
        "From Doppler's secrets.update webhook: one change per name added, "
        "removed or updated, with the config and when it arrived. since is "
        "inclusive and until exclusive. Names only; no value is ever received. "
        "meta.total counts every match, across pages."
    ),
)
async def list_secret_changes(
    config: Annotated[RotationConfig | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(require_scope("secrets.changes.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[SecretChange]]:
    """The changes that match, oldest first."""
    changes, total = await secret_changes.list_changes(
        session, config=config, since=since, until=until, limit=limit, offset=offset
    )
    return success_envelope(changes, count=len(changes), total=total)
