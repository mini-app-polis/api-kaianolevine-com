"""The task board's grooming records, and the month's paid grooming.

task-manager-cog is the only writer of records: it decides what a sweep
made of each task, and this stores it. See
``mini_app_polis.api.contract.groomings`` for what each field means.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
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
