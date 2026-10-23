"""The agent runs that implement Ready tasks, and the month's agent spend.

task-manager-cog records a run before it starts one, and a run still
``dispatched`` is the lock on its repository: the table allows one per
repository. A run ends once, with its workflow's result or with
task-manager-cog marking it failed. A run that needs grooming ends with
the questions it asked, for task-manager-cog to post on the task. See
``mini_app_polis.api.contract.agent_runs`` for what each field means.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    AgentRun,
    AgentRunCreate,
    AgentRunFailure,
    AgentRunResult,
    AgentSpend,
)

_COLUMNS = (
    "id, task_gid, task_id, repository, model, status, cost_usd, pr_url, "
    "actions_run_url, reason, questions, dispatched_at, finished_at"
)


class RunInFlight(Exception):
    """The repository already has a run in flight."""


class RunNotFound(Exception):
    """No run has that id."""


class RunAlreadyEnded(Exception):
    """The run has ended: its result or failure is already recorded."""


def _run(row: Any) -> AgentRun:
    data = dict(row._mapping)
    data["id"] = str(data["id"])
    if data["cost_usd"] is not None:
        data["cost_usd"] = float(data["cost_usd"])
    questions = data["questions"]
    data["questions"] = (
        json.loads(questions) if isinstance(questions, str) else questions
    )
    return AgentRun.model_validate(data)


async def create_run(session: AsyncSession, run: AgentRunCreate) -> AgentRun:
    """Record a run about to be started. Raises RunInFlight."""
    try:
        row = (
            await session.execute(
                text(
                    f"""
                    INSERT INTO task_agent_runs (task_gid, task_id, repository, model)
                    VALUES (:task_gid, :task_id, :repository, :model)
                    RETURNING {_COLUMNS}
                    """
                ),
                run.model_dump(),
            )
        ).one()
    except IntegrityError as exc:
        await session.rollback()
        raise RunInFlight(run.repository) from exc
    await session.commit()
    return _run(row)


async def list_runs(
    session: AsyncSession,
    *,
    status: str | None = None,
    repository: str | None = None,
) -> list[AgentRun]:
    """Runs, newest first, narrowed by status and repository when given."""
    rows = await session.execute(
        text(
            f"""
            SELECT {_COLUMNS} FROM task_agent_runs
            WHERE (CAST(:status AS TEXT) IS NULL OR status = :status)
              AND (CAST(:repository AS TEXT) IS NULL OR repository = :repository)
            ORDER BY dispatched_at DESC
            LIMIT 500
            """
        ),
        {"status": status, "repository": repository},
    )
    return [_run(row) for row in rows]


async def _end(
    session: AsyncSession,
    run_id: str,
    *,
    status: str,
    cost_usd: float | None,
    pr_url: str | None,
    actions_run_url: str | None,
    reason: str | None,
    questions: list[str],
) -> AgentRun:
    """End a dispatched run, uncommitted. Raises RunNotFound, RunAlreadyEnded."""
    row = (
        await session.execute(
            text(
                f"""
                UPDATE task_agent_runs SET
                  status = :status,
                  cost_usd = :cost_usd,
                  pr_url = :pr_url,
                  actions_run_url = coalesce(:actions_run_url, actions_run_url),
                  reason = :reason,
                  questions = CAST(:questions AS jsonb),
                  finished_at = now()
                WHERE id = CAST(:id AS uuid) AND status = 'dispatched'
                RETURNING {_COLUMNS}
                """
            ),
            {
                "id": run_id,
                "status": status,
                "cost_usd": cost_usd,
                "pr_url": pr_url,
                "actions_run_url": actions_run_url,
                "reason": reason,
                "questions": json.dumps(questions),
            },
        )
    ).one_or_none()
    if row is not None:
        return _run(row)
    exists = (
        await session.execute(
            text("SELECT 1 FROM task_agent_runs WHERE id = CAST(:id AS uuid)"),
            {"id": run_id},
        )
    ).one_or_none()
    raise RunAlreadyEnded(run_id) if exists else RunNotFound(run_id)


async def fail_run(session: AsyncSession, failure: AgentRunFailure) -> AgentRun:
    """Mark a dispatched run failed, its cost unknown."""
    run = await _end(
        session,
        failure.run_id,
        status="failed",
        cost_usd=None,
        pr_url=None,
        actions_run_url=None,
        reason=failure.reason,
        questions=[],
    )
    await session.commit()
    return run


async def end_run(session: AsyncSession, result: AgentRunResult) -> AgentRun:
    """Record a run's result, uncommitted: the caller commits once the
    result is on the task manager's queue, so a result that never reaches
    the board is not recorded either."""
    return await _end(
        session,
        result.run_id,
        status=result.outcome,
        cost_usd=result.cost_usd,
        pr_url=result.pr_url,
        actions_run_url=result.actions_run_url,
        reason=result.reason,
        questions=result.questions,
    )


async def month_spend(session: AsyncSession) -> AgentSpend:
    """The calendar month's runs and their known cost so far, in UTC."""
    row = (
        await session.execute(
            text(
                """
                SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM') AS month,
                       count(*) AS runs,
                       coalesce(sum(cost_usd), 0) AS cost_usd
                FROM task_agent_runs
                WHERE dispatched_at >=
                  date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                """
            )
        )
    ).one()
    return AgentSpend(month=row.month, runs=row.runs, cost_usd=float(row.cost_usd))
