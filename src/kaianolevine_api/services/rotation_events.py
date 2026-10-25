"""The record of secret rotations, written by the rotator.

One event per rotation of one secret in one config, keyed on the secret's
name, the config and when the new value was minted. Writing the same key
again updates the event: the rotator writes it once the new value has been
verified, and again with ``retired`` once the old value is gone. These events
are the only record of what was rotated, so nothing here deletes one. The
daily sweep reads the latest event per name and config to decide what is
due. See ``mini_app_polis.api.contract.secret_rotations`` for what each field
means.

No column holds a secret value: names, configs, kinds, people and times only,
and the table's checks refuse what the contract's patterns refuse.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import RotationConfig, RotationEvent, RotationEventCreate

_COLUMNS = (
    "id, name, config, kind, mode, actor, minted, retired, verification, "
    "verification_reason, aws_key_last4, recorded_at"
)


def _event(row: Any) -> RotationEvent:
    data = dict(row._mapping)
    data["id"] = str(data["id"])
    return RotationEvent.model_validate(data)


async def record_event(
    session: AsyncSession, event: RotationEventCreate
) -> RotationEvent:
    """Write one event, updating the one with the same key if it exists."""
    row = (
        await session.execute(
            text(
                f"""
                INSERT INTO rotation_events
                  (name, config, kind, mode, actor, minted, retired,
                   verification, verification_reason, aws_key_last4,
                   recorded_at)
                VALUES (:name, :config, :kind, :mode, :actor, :minted,
                        :retired, :verification, :verification_reason,
                        :aws_key_last4, now())
                ON CONFLICT (name, config, minted) DO UPDATE SET
                  kind = EXCLUDED.kind,
                  mode = EXCLUDED.mode,
                  actor = EXCLUDED.actor,
                  retired = EXCLUDED.retired,
                  verification = EXCLUDED.verification,
                  verification_reason = EXCLUDED.verification_reason,
                  aws_key_last4 = EXCLUDED.aws_key_last4,
                  recorded_at = now()
                RETURNING {_COLUMNS}
                """
            ),
            event.model_dump(),
        )
    ).one()
    await session.commit()
    return _event(row)


async def list_events(
    session: AsyncSession,
    *,
    name: str | None = None,
    config: RotationConfig | None = None,
    latest: bool = False,
    limit: int,
    offset: int = 0,
) -> tuple[list[RotationEvent], int]:
    """Events, newest minted first, and how many match in all.

    ``latest`` keeps only the newest event for each name and config.
    """
    where = """
        WHERE (CAST(:name AS TEXT) IS NULL OR name = :name)
          AND (CAST(:config AS TEXT) IS NULL OR config = :config)
    """
    if latest:
        source = f"""
            (SELECT DISTINCT ON (name, config) {_COLUMNS}
               FROM rotation_events {where}
              ORDER BY name, config, minted DESC) AS events
        """
    else:
        source = f"rotation_events {where}"
    params: dict[str, Any] = {"name": name, "config": config}
    total = (
        await session.execute(text(f"SELECT count(*) FROM {source}"), params)
    ).scalar_one()
    rows = await session.execute(
        text(
            f"""
            SELECT {_COLUMNS} FROM {source}
            ORDER BY minted DESC, name, config
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    )
    return [_event(row) for row in rows], int(total)
