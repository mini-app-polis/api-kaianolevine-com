"""When a machine last authenticated with its previous key.

While a rotation overlaps, the API accepts a machine's previous key beside
its current one, and each audit event for a key-authenticated request
records which slot matched (identity_audit_events.key_slot, migration 073).
The rotator retires a previous key once nothing has presented it for a day,
and this is how it knows. Only this API's own requests are seen: a
development API keeps its own audit trail.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

_AUDIT = table(
    "identity_audit_events",
    column("subject"),
    column("key_slot"),
    column("occurred_at"),
)


async def last_previous_use(session: AsyncSession, machine: str) -> dt.datetime | None:
    """The newest request ``machine`` proved with its previous key, or None."""
    result = await session.execute(
        select(func.max(_AUDIT.c.occurred_at)).where(
            _AUDIT.c.subject == machine, _AUDIT.c.key_slot == "previous"
        )
    )
    last: dt.datetime | None = result.scalar_one_or_none()
    return last
