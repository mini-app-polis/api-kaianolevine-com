"""Rotation incidents: every secret rotated, compromised, under one id.

The owner declares an incident through ``POST /v1/secrets/rotation-incidents``.
This module mints its id and start time, and the route queues one
``rotation.incident`` for rotator-cog (services/rotation_dispatch.py). The
rotator registers the incident's entries (its inventory as it stood when the
incident was declared), queues a compromised ``rotation.job`` for each secret
it rotates, and asks for a board task for each one a person rotates, marking
the entry here once the ask is accepted.

An entry's state is derived, never stored. In precedence order:

``rotated``
    a rotation event for its name and config with verification ``ok`` or
    ``unchecked`` was minted after the incident started, tagged with the
    incident or not (so a hand-run ``scripts/rotate.py`` rotation counts);
``failed``
    the latest such event since the start failed;
``task-opened``
    its board task has been asked for;
``outstanding``
    none of those yet.

The read never calls the board or the queue: the registered entries, the
events and the task flag are all it needs. No row or answer holds a secret
value. See ``mini_app_polis.api.contract.secret_rotations``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    RotationConfig,
    RotationIncident,
    RotationIncidentEntryStatus,
    RotationIncidentRegistration,
)

#: Where an unrotated entry sorts in the outstanding list: failed first.
_OUTSTANDING_RANK = {"failed": 0, "task-opened": 1, "outstanding": 1}
_CONFIG_RANK = {"prd": 0, "dev": 1}

# Each registered entry with its state, in registration order. The two
# lateral reads look only at events minted at or after the incident started.
_ENTRIES = """
    SELECT e.name, e.config, e.tier, e.handling, e.task_requested,
           CASE
             WHEN ok.found THEN 'rotated'
             WHEN latest.verification = 'failed' THEN 'failed'
             WHEN e.task_requested THEN 'task-opened'
             ELSE 'outstanding'
           END AS state
      FROM rotation_incident_entries e
      JOIN rotation_incidents i ON i.id = e.incident_id
      CROSS JOIN LATERAL (
        SELECT EXISTS (
          SELECT 1 FROM rotation_events r
           WHERE r.name = e.name AND r.config = e.config
             AND r.minted >= i.started_at
             AND r.verification IN ('ok', 'unchecked')
        ) AS found
      ) ok
      LEFT JOIN LATERAL (
        SELECT r.verification FROM rotation_events r
         WHERE r.name = e.name AND r.config = e.config
           AND r.minted >= i.started_at
         ORDER BY r.minted DESC
         LIMIT 1
      ) latest ON true
     WHERE e.incident_id = CAST(:incident_id AS UUID)
"""


class IncidentNotFound(LookupError):
    """No incident has this id."""


class EntryNotFound(LookupError):
    """The incident has no entry for this name and config."""


async def start_incident(session: AsyncSession) -> tuple[str, datetime]:
    """Record a new incident; returns its id and start time.

    Committed before anything is queued, so the rotator can register the
    incident as soon as it receives it.
    """
    row = (
        await session.execute(
            text(
                "INSERT INTO rotation_incidents DEFAULT VALUES RETURNING id, started_at"
            )
        )
    ).one()
    await session.commit()
    return str(row.id), row.started_at


async def started_at(session: AsyncSession, incident_id: str) -> datetime:
    """When the incident started. Raises IncidentNotFound."""
    value = (
        await session.execute(
            text(
                "SELECT started_at FROM rotation_incidents "
                "WHERE id = CAST(:incident_id AS UUID)"
            ),
            {"incident_id": incident_id},
        )
    ).scalar_one_or_none()
    if value is None:
        raise IncidentNotFound(incident_id)
    return value


async def register_entries(
    session: AsyncSession, incident_id: str, registration: RotationIncidentRegistration
) -> RotationIncident:
    """Register the incident's entries, replacing an earlier registration.

    An entry registered again keeps what is recorded for it (its task flag);
    an entry no longer in the list is dropped. Raises IncidentNotFound.
    """
    await started_at(session, incident_id)
    keys = [(entry.name, entry.config) for entry in registration.entries]
    await session.execute(
        text(
            """
            DELETE FROM rotation_incident_entries
             WHERE incident_id = CAST(:incident_id AS UUID)
               AND NOT ((name, config) IN (
                 SELECT * FROM unnest(CAST(:names AS TEXT[]), CAST(:configs AS TEXT[]))
               ))
            """
        ),
        {
            "incident_id": incident_id,
            "names": [name for name, _ in keys],
            "configs": [config for _, config in keys],
        },
    )
    for position, entry in enumerate(registration.entries):
        await session.execute(
            text(
                """
                INSERT INTO rotation_incident_entries
                  (incident_id, name, config, tier, handling, position)
                VALUES (CAST(:incident_id AS UUID), :name, :config, :tier,
                        :handling, :position)
                ON CONFLICT (incident_id, name, config) DO UPDATE SET
                  tier = EXCLUDED.tier,
                  handling = EXCLUDED.handling,
                  position = EXCLUDED.position
                """
            ),
            {"incident_id": incident_id, "position": position, **entry.model_dump()},
        )
    await session.commit()
    return await read_incident(session, incident_id)


async def mark_task_requested(
    session: AsyncSession, incident_id: str, name: str, config: RotationConfig
) -> RotationIncidentEntryStatus:
    """Record that the entry's board task has been asked for. Idempotent.

    Raises IncidentNotFound, or EntryNotFound for an entry not registered.
    """
    await started_at(session, incident_id)
    updated = (
        await session.execute(
            text(
                """
                UPDATE rotation_incident_entries SET task_requested = true
                 WHERE incident_id = CAST(:incident_id AS UUID)
                   AND name = :name AND config = :config
                RETURNING name
                """
            ),
            {"incident_id": incident_id, "name": name, "config": config},
        )
    ).scalar_one_or_none()
    if updated is None:
        await session.rollback()
        raise EntryNotFound(f"{name} in {config}")
    await session.commit()
    row = (
        await session.execute(
            text(_ENTRIES + " AND e.name = :name AND e.config = :config"),
            {"incident_id": incident_id, "name": name, "config": config},
        )
    ).one()
    return _status(row)


def _status(row: Any) -> RotationIncidentEntryStatus:
    return RotationIncidentEntryStatus.model_validate(dict(row._mapping))


def outstanding_order(
    entries: list[RotationIncidentEntryStatus],
) -> list[RotationIncidentEntryStatus]:
    """Every entry not rotated: failed first, then prd before dev, then by
    tier, then in registration order."""
    pending = [(i, e) for i, e in enumerate(entries) if e.state != "rotated"]
    pending.sort(
        key=lambda pair: (
            _OUTSTANDING_RANK[pair[1].state],
            _CONFIG_RANK[pair[1].config],
            pair[1].tier,
            pair[0],
        )
    )
    return [entry for _, entry in pending]


async def read_incident(session: AsyncSession, incident_id: str) -> RotationIncident:
    """The incident, each registered entry's state, and what is outstanding.

    Raises IncidentNotFound.
    """
    start = await started_at(session, incident_id)
    rows = await session.execute(
        text(_ENTRIES + " ORDER BY e.position"), {"incident_id": incident_id}
    )
    entries = [_status(row) for row in rows]
    return RotationIncident(
        incident_id=incident_id,
        started_at=start,
        entries=entries,
        outstanding=outstanding_order(entries),
    )
