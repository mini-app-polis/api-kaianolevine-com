"""Persistence helper for eval-run rows.

Single-purpose: write one (run_id, question_id) row to wcs_qa_eval_runs. The
table is append-only — manual_grade and manual_grade_notes are reserved for
the v1.1 admin UI.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.models import WcsQaEvalRun


async def write_eval_run(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    question_id: str,
    git_sha: str,
    agent_answer: str,
    cited_source_ids: dict,
    tool_trace: dict,
    source_recall: float | None,
    source_precision: float | None,
    judge_score: int | None,
    judge_reasoning: str | None,
    judge_model: str,
    judge_prompt_sha: str,
) -> None:
    session.add(
        WcsQaEvalRun(
            run_id=run_id,
            question_id=question_id,
            git_sha=git_sha,
            agent_answer=agent_answer,
            cited_source_ids=cited_source_ids,
            tool_trace=tool_trace,
            source_recall=source_recall,
            source_precision=source_precision,
            judge_score=judge_score,
            judge_reasoning=judge_reasoning,
            judge_model=judge_model,
            judge_prompt_sha=judge_prompt_sha,
        )
    )
    await session.commit()
