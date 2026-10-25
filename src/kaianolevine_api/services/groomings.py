"""The task board's grooming records, and the month's paid grooming.

task-manager-cog is the only writer of records: it decides what a sweep
made of each task, and this stores it. task-groomer-cog claims each job
before its model call, so a job delivered twice is groomed once. See
``mini_app_polis.api.contract.groomings`` for what each field means.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    GroomingClaimReleased,
    GroomingClaimRequest,
    GroomingClaimResult,
    GroomingRecord,
    GroomingRecordWrite,
    GroomingRunCreate,
    GroomingRunRecorded,
    GroomingSpend,
)

_COLUMNS = (
    "task_gid, task_id, fingerprint, outcome, problems, moved_to_ready_at, "
    "returned_count, groomed_fingerprint, updated_at"
)


def _record(row: Any) -> GroomingRecord:
    data = dict(row._mapping)
    problems = data["problems"]
    data["problems"] = json.loads(problems) if isinstance(problems, str) else problems
    return GroomingRecord.model_validate(data)


async def list_records(session: AsyncSession) -> list[GroomingRecord]:
    """Every record, in a stable order."""
    rows = await session.execute(
        text(f"SELECT {_COLUMNS} FROM task_groomings ORDER BY task_gid")
    )
    return [_record(row) for row in rows]


async def write_record(
    session: AsyncSession, record: GroomingRecordWrite
) -> GroomingRecord:
    """Write one task's record, replacing any it had."""
    row = (
        await session.execute(
            text(
                f"""
                INSERT INTO task_groomings
                  (task_gid, task_id, fingerprint, outcome, problems,
                   moved_to_ready_at, returned_count, groomed_fingerprint,
                   updated_at)
                VALUES (:task_gid, :task_id, :fingerprint, :outcome,
                        CAST(:problems AS jsonb), :moved_to_ready_at,
                        :returned_count, :groomed_fingerprint, now())
                ON CONFLICT (task_gid) DO UPDATE SET
                  task_id = EXCLUDED.task_id,
                  fingerprint = EXCLUDED.fingerprint,
                  outcome = EXCLUDED.outcome,
                  problems = EXCLUDED.problems,
                  moved_to_ready_at = EXCLUDED.moved_to_ready_at,
                  returned_count = EXCLUDED.returned_count,
                  groomed_fingerprint = EXCLUDED.groomed_fingerprint,
                  updated_at = now()
                RETURNING {_COLUMNS}
                """
            ),
            {
                **record.model_dump(exclude={"problems"}),
                "problems": json.dumps(record.problems),
            },
        )
    ).one()
    await session.commit()
    return _record(row)


async def record_run(
    session: AsyncSession, run: GroomingRunCreate
) -> GroomingRunRecorded:
    """Record one paid run."""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO task_grooming_runs
                  (task_gid, model, input_tokens, output_tokens, cost_usd)
                VALUES (:task_gid, :model, :input_tokens, :output_tokens, :cost_usd)
                RETURNING id, task_gid, cost_usd, recorded_at
                """
            ),
            run.model_dump(),
        )
    ).one()
    await session.commit()
    return GroomingRunRecorded(
        id=str(row.id),
        task_gid=row.task_gid,
        cost_usd=float(row.cost_usd),
        recorded_at=row.recorded_at,
    )


async def month_spend(session: AsyncSession) -> GroomingSpend:
    """The calendar month's paid runs so far, in UTC."""
    row = (
        await session.execute(
            text(
                """
                SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM') AS month,
                       count(*) AS runs,
                       coalesce(sum(cost_usd), 0) AS cost_usd
                FROM task_grooming_runs
                WHERE recorded_at >=
                  date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                """
            )
        )
    ).one()
    return GroomingSpend(month=row.month, runs=row.runs, cost_usd=float(row.cost_usd))


async def claim(
    session: AsyncSession, key: GroomingClaimRequest
) -> GroomingClaimResult:
    """Claim one job for its task and content; won only if nobody holds it.

    One statement, so the primary key decides between two callers claiming
    at once: the first insert wins and the other conflicts. A conflict on a
    released claim takes it again; one on a held claim changes nothing.
    """
    row = (
        await session.execute(
            text(
                """
                INSERT INTO task_grooming_claims (task_gid, fingerprint)
                VALUES (:task_gid, :fingerprint)
                ON CONFLICT (task_gid, fingerprint) DO UPDATE SET
                  state = 'claimed',
                  claimed_at = now(),
                  released_at = NULL
                WHERE task_grooming_claims.state = 'released'
                RETURNING task_gid
                """
            ),
            key.model_dump(),
        )
    ).first()
    await session.commit()
    return GroomingClaimResult(**key.model_dump(), claimed=row is not None)


async def release_claim(
    session: AsyncSession, key: GroomingClaimRequest
) -> GroomingClaimReleased:
    """Give a held claim back, so the same content can be claimed again."""
    row = (
        await session.execute(
            text(
                """
                UPDATE task_grooming_claims
                SET state = 'released', released_at = now()
                WHERE task_gid = :task_gid
                  AND fingerprint = :fingerprint
                  AND state = 'claimed'
                RETURNING task_gid
                """
            ),
            key.model_dump(),
        )
    ).first()
    await session.commit()
    return GroomingClaimReleased(**key.model_dump(), released=row is not None)
