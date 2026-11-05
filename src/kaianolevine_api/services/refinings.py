"""The task board's refining records, and the month's paid refining.

task-manager-cog is the only writer of records: it decides what a sweep
made of each task, and this stores it. task-refiner-cog claims each job
before its model call, so a job delivered twice is refined once, and
reports a run the spent budget stopped, which a later won claim clears. See
``mini_app_polis.api.contract.refinings`` for what each field means.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    GroomingRecord,
    GroomingRecordWrite,
    RefiningBudgetSpentMarked,
    RefiningBudgetSpentRequest,
    RefiningClaimReleased,
    RefiningClaimRequest,
    RefiningClaimResult,
    RefiningRecord,
    RefiningRecordWrite,
    RefiningReturnRate,
    RefiningRunCreate,
    RefiningRunRecorded,
    RefiningSpend,
)

_COLUMNS = (
    "task_gid, task_id, fingerprint, outcome, problems, moved_to_ready_at, "
    "returned_count, refined_fingerprint, refine_sent_at, refine_attempts, "
    "updated_at"
)

# A record as served: its columns, and the budget stop kept apart from it.
_SERVED = (
    "g.task_gid, g.task_id, g.fingerprint, g.outcome, g.problems, "
    "g.moved_to_ready_at, g.returned_count, g.refined_fingerprint, "
    "g.refine_sent_at, g.refine_attempts, "
    "g.updated_at, s.fingerprint AS budget_spent_fingerprint"
)


def _record(row: Any) -> RefiningRecord:
    data = dict(row._mapping)
    problems = data["problems"]
    data["problems"] = json.loads(problems) if isinstance(problems, str) else problems
    return RefiningRecord.model_validate(data)


async def list_records(session: AsyncSession) -> list[RefiningRecord]:
    """Every record, in a stable order."""
    rows = await session.execute(
        text(
            f"""
            SELECT {_SERVED}
            FROM task_refinings g
            LEFT JOIN task_refining_budget_stops s USING (task_gid)
            ORDER BY g.task_gid
            """
        )
    )
    return [_record(row) for row in rows]


async def write_record(
    session: AsyncSession, record: RefiningRecordWrite
) -> RefiningRecord:
    """Write one task's record, replacing any it had.

    The budget stop is not part of the write: it is served as stored. A
    record that sets ``moved_to_ready_at`` to a new time records a move to
    Ready, and one that raises ``returned_count`` a return for each step
    (task_ready_events), in the same transaction.
    """
    previous = (
        await session.execute(
            text(
                "SELECT moved_to_ready_at, returned_count FROM task_refinings "
                "WHERE task_gid = :task_gid FOR UPDATE"
            ),
            {"task_gid": record.task_gid},
        )
    ).first()
    await _record_ready_events(session, record, previous)
    row = (
        await session.execute(
            text(
                f"""
                WITH g AS (
                  INSERT INTO task_refinings
                    (task_gid, task_id, fingerprint, outcome, problems,
                     moved_to_ready_at, returned_count, refined_fingerprint,
                     refine_sent_at, refine_attempts, updated_at)
                  VALUES (:task_gid, :task_id, :fingerprint, :outcome,
                          CAST(:problems AS jsonb), :moved_to_ready_at,
                          :returned_count, :refined_fingerprint,
                          :refine_sent_at, :refine_attempts, now())
                  ON CONFLICT (task_gid) DO UPDATE SET
                    task_id = EXCLUDED.task_id,
                    fingerprint = EXCLUDED.fingerprint,
                    outcome = EXCLUDED.outcome,
                    problems = EXCLUDED.problems,
                    moved_to_ready_at = EXCLUDED.moved_to_ready_at,
                    returned_count = EXCLUDED.returned_count,
                    refined_fingerprint = EXCLUDED.refined_fingerprint,
                    refine_sent_at = EXCLUDED.refine_sent_at,
                    refine_attempts = EXCLUDED.refine_attempts,
                    updated_at = now()
                  RETURNING {_COLUMNS}
                )
                SELECT {_SERVED}
                FROM g LEFT JOIN task_refining_budget_stops s USING (task_gid)
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


async def _record_ready_events(
    session: AsyncSession, record: RefiningRecordWrite, previous: Any
) -> None:
    """The moves to Ready and returns from it that this write records."""
    moved_before = previous.moved_to_ready_at if previous is not None else None
    if record.moved_to_ready_at is not None and (
        moved_before is None or record.moved_to_ready_at != moved_before
    ):
        await session.execute(
            text(
                "INSERT INTO task_ready_events (task_gid, kind, occurred_at) "
                "VALUES (:task_gid, 'moved_to_ready', :at)"
            ),
            {"task_gid": record.task_gid, "at": record.moved_to_ready_at},
        )
    returned_before = previous.returned_count if previous is not None else 0
    for _ in range(max(0, record.returned_count - returned_before)):
        await session.execute(
            text(
                "INSERT INTO task_ready_events (task_gid, kind) "
                "VALUES (:task_gid, 'returned')"
            ),
            {"task_gid": record.task_gid},
        )


#: The migration whose application is when task_ready_events starts.
READY_EVENTS_MIGRATION = "083_task_ready_events.sql"


async def return_rate(session: AsyncSession, days: int) -> RefiningReturnRate:
    """Moves to Ready and returns from it over the last ``days``, and their ratio.

    Counted from the window's start, or from when task_ready_events began
    (its migration's application) when that is later.
    """
    row = (
        await session.execute(
            text(
                """
                WITH bounds AS (
                  SELECT now() - make_interval(days => :days) AS since,
                         (SELECT applied_at FROM schema_migrations
                          WHERE filename = :migration) AS started
                ), window_ AS (
                  SELECT since, GREATEST(since, COALESCE(started, since)) AS counting_since
                  FROM bounds
                )
                SELECT w.since, w.counting_since,
                       count(*) FILTER (WHERE e.kind = 'moved_to_ready') AS moved,
                       count(*) FILTER (WHERE e.kind = 'returned') AS returned
                FROM window_ w
                LEFT JOIN task_ready_events e ON e.occurred_at >= w.counting_since
                GROUP BY w.since, w.counting_since
                """
            ),
            {"days": days, "migration": READY_EVENTS_MIGRATION},
        )
    ).one()
    moved, returned = int(row.moved), int(row.returned)
    return RefiningReturnRate(
        days=days,
        since=row.since,
        counting_since=row.counting_since,
        moved_to_ready=moved,
        returned=returned,
        ratio=returned / moved if moved else None,
    )


async def record_run(
    session: AsyncSession, run: RefiningRunCreate
) -> RefiningRunRecorded:
    """Record one paid run."""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO task_refining_runs
                  (task_gid, model, input_tokens, output_tokens, cost_usd)
                VALUES (:task_gid, :model, :input_tokens, :output_tokens, :cost_usd)
                RETURNING id, task_gid, cost_usd, recorded_at
                """
            ),
            run.model_dump(),
        )
    ).one()
    await session.commit()
    return RefiningRunRecorded(
        id=str(row.id),
        task_gid=row.task_gid,
        cost_usd=float(row.cost_usd),
        recorded_at=row.recorded_at,
    )


async def month_spend(session: AsyncSession) -> RefiningSpend:
    """The calendar month's paid runs so far, in UTC."""
    row = (
        await session.execute(
            text(
                """
                SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM') AS month,
                       count(*) AS runs,
                       coalesce(sum(cost_usd), 0) AS cost_usd
                FROM task_refining_runs
                WHERE recorded_at >=
                  date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                """
            )
        )
    ).one()
    return RefiningSpend(month=row.month, runs=row.runs, cost_usd=float(row.cost_usd))


async def claim(
    session: AsyncSession, key: RefiningClaimRequest
) -> RefiningClaimResult:
    """Claim one job for its task and content; won only if nobody holds it.

    One statement, so the primary key decides between two callers claiming
    at once: the first insert wins and the other conflicts. A conflict on a
    released claim takes it again; one on a held claim changes nothing.
    A won claim clears the task's budget stop when it was for this content.
    """
    row = (
        await session.execute(
            text(
                """
                WITH won AS (
                  INSERT INTO task_refining_claims (task_gid, fingerprint)
                  VALUES (:task_gid, :fingerprint)
                  ON CONFLICT (task_gid, fingerprint) DO UPDATE SET
                    state = 'claimed',
                    claimed_at = now(),
                    released_at = NULL
                  WHERE task_refining_claims.state = 'released'
                  RETURNING task_gid, fingerprint
                ), cleared AS (
                  DELETE FROM task_refining_budget_stops s
                  USING won
                  WHERE s.task_gid = won.task_gid
                    AND s.fingerprint = won.fingerprint
                )
                SELECT task_gid FROM won
                """
            ),
            key.model_dump(),
        )
    ).first()
    await session.commit()
    return RefiningClaimResult(**key.model_dump(), claimed=row is not None)


async def release_claim(
    session: AsyncSession, key: RefiningClaimRequest
) -> RefiningClaimReleased:
    """Give a held claim back, so the same content can be claimed again."""
    row = (
        await session.execute(
            text(
                """
                UPDATE task_refining_claims
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
    return RefiningClaimReleased(**key.model_dump(), released=row is not None)


async def mark_budget_spent(
    session: AsyncSession, stop: RefiningBudgetSpentRequest
) -> RefiningBudgetSpentMarked:
    """Store a run the spent budget stopped, replacing the task's last stop.

    Kept apart from the record, so it can be marked before a sweep has
    written one.
    """
    row = (
        await session.execute(
            text(
                """
                INSERT INTO task_refining_budget_stops (task_gid, fingerprint)
                VALUES (:task_gid, :fingerprint)
                ON CONFLICT (task_gid) DO UPDATE SET
                  fingerprint = EXCLUDED.fingerprint,
                  marked_at = now()
                RETURNING task_gid, fingerprint
                """
            ),
            stop.model_dump(),
        )
    ).one()
    await session.commit()
    return RefiningBudgetSpentMarked(task_gid=row.task_gid, fingerprint=row.fingerprint)


# The grooming names. The /v1/tasks/groomings routes speak the record under
# its old field names until every cog has moved to refining; these turn one
# shape into the other. Removed with those routes.

#: Each record field's grooming name, by its refining name.
GROOMING_FIELD_NAMES = {
    "refined_fingerprint": "groomed_fingerprint",
    "refine_sent_at": "groom_sent_at",
    "refine_attempts": "groom_attempts",
}


def as_grooming_record(record: RefiningRecord) -> GroomingRecord:
    """A refining record under its grooming field names."""
    data = record.model_dump()
    return GroomingRecord.model_validate(
        {GROOMING_FIELD_NAMES.get(name, name): value for name, value in data.items()}
    )


def from_grooming_write(record: GroomingRecordWrite) -> RefiningRecordWrite:
    """A record written under its grooming field names, as a refining one."""
    refining_names = {old: new for new, old in GROOMING_FIELD_NAMES.items()}
    data = record.model_dump()
    return RefiningRecordWrite.model_validate(
        {refining_names.get(name, name): value for name, value in data.items()}
    )
