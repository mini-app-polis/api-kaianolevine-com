"""Secret names Doppler reports changed, from its secrets.update webhook.

A delivery lists the names added, removed and updated in one config, and no
values. It is stored once, keyed by a hash of its body, because Doppler may
deliver it more than once. Each name becomes one change, and rotator-cog's
sweep compares the changes with the rotation record. Only names, the config
and when the change arrived are kept: neither the body nor any other field
of it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, get_args

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import RotationConfig, SecretChange, SecretChangeKind

_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CONFIGS: frozenset[str] = frozenset(get_args(RotationConfig))
_KINDS: tuple[str, ...] = get_args(SecretChangeKind)


def changed_names(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """(change, name) for every well-formed name in the delivery's diff."""
    diff = payload.get("diff")
    if not isinstance(diff, dict):
        return []
    out: list[tuple[str, str]] = []
    for kind in _KINDS:
        names = diff.get(kind)
        if not isinstance(names, list):
            continue
        for name in names:
            if isinstance(name, str) and len(name) <= 128 and _NAME.match(name):
                out.append((kind, name))
    return out


def config_of(payload: dict[str, Any]) -> str | None:
    """The delivery's config name, if it is one the record keeps."""
    config = payload.get("config")
    name = config.get("name") if isinstance(config, dict) else None
    return name if name in _CONFIGS else None


def project_of(payload: dict[str, Any]) -> str | None:
    """The delivery's project, by slug where Doppler sends one, else by name."""
    for source in (
        payload.get("project"),
        (payload.get("config") or {}).get("project"),
    ):
        if isinstance(source, dict):
            for key in ("slug", "name", "id"):
                if isinstance(source.get(key), str):
                    return source[key]
        elif isinstance(source, str):
            return source
    return None


async def record_delivery(
    session: AsyncSession,
    *,
    raw_body: bytes,
    config: str,
    changes: list[tuple[str, str]],
) -> int:
    """Store one delivery's changes; a delivery already stored adds nothing.

    Returns how many changes were stored.
    """
    digest = hashlib.sha256(raw_body).hexdigest()
    delivery = (
        await session.execute(
            text(
                """
                INSERT INTO doppler_deliveries (body_sha256)
                VALUES (:digest)
                ON CONFLICT (body_sha256) DO NOTHING
                RETURNING id, received_at
                """
            ),
            {"digest": digest},
        )
    ).first()
    if delivery is None:
        await session.commit()
        return 0
    for change, name in changes:
        await session.execute(
            text(
                """
                INSERT INTO secret_changes
                  (delivery_id, name, config, change, changed_at)
                VALUES (:delivery_id, :name, :config, :change, :changed_at)
                ON CONFLICT (delivery_id, name, change) DO NOTHING
                """
            ),
            {
                "delivery_id": delivery.id,
                "name": name,
                "config": config,
                "change": change,
                "changed_at": delivery.received_at,
            },
        )
    await session.commit()
    return len(changes)


async def list_changes(
    session: AsyncSession,
    *,
    config: RotationConfig | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int,
    offset: int = 0,
) -> tuple[list[SecretChange], int]:
    """Changes, oldest first, and how many match in all."""
    where = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if config is not None:
        where.append("config = :config")
        params["config"] = config
    if since is not None:
        where.append("changed_at >= :since")
        params["since"] = since
    if until is not None:
        where.append("changed_at < :until")
        params["until"] = until
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = (
        await session.execute(
            text(f"SELECT count(*) FROM secret_changes {clause}"), params
        )
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                f"""
                SELECT name, config, change, changed_at, delivery_id AS received_id
                FROM secret_changes {clause}
                ORDER BY changed_at, name, change
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).all()
    changes = [
        SecretChange.model_validate(
            {**dict(row._mapping), "received_id": str(row.received_id)}
        )
        for row in rows
    ]
    return changes, int(total)
