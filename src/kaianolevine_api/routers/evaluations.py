from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from mini_app_polis.logger import get_logger
from sqlalchemy import case, func, select, union
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import get_settings
from ..database import get_db_session
from ..models import PipelineEvaluation as DbEval
from ..models import StandardsCatalog as DbCatalog
from ..schemas import (
    Envelope,
    EvaluationFleetAccepted,
    EvaluationFleetRepo,
    EvaluationFleetRequest,
    EvaluationIntrospectionAccepted,
    EvaluationIntrospectionRequest,
    EvaluationRunAccepted,
    EvaluationRunRequest,
    EvaluationSummaryItem,
    PipelineEvaluationCreate,
    PipelineEvaluationItem,
    PipelineEvaluationWriteResult,
    api_error,
    success_envelope,
)
from ..services import evaluation_dispatch
from ..services.evaluation_fingerprint import evaluation_fingerprint

logger = get_logger()

router = APIRouter()


# Authoritative per-row timestamp expression.
#
# Background: migration 002_expand_evaluations.sql added `evaluated_at` as a
# nullable column (TIMESTAMPTZ DEFAULT now()), even though the SQLAlchemy model
# declares it as nullable=False. Pre-002 rows therefore exist in production
# with evaluated_at = NULL, while still having a populated `created_at` (which
# migration 001 created as NOT NULL DEFAULT now()).
#
# Rather than ship another migration, fall back to created_at whenever
# evaluated_at is NULL so the API never surfaces a null timestamp and ordering
# stays deterministic for legacy rows.
_EVALUATED_AT = func.coalesce(DbEval.evaluated_at, DbEval.created_at)


def _eligible_latest_evaluation_ids_subquery():
    """
    Rows that belong to the latest "run" per (repo, source).

    Prefer run_id: at max(evaluated_at), take distinct non-null run_id; return all
    rows with that run_id for the repo+source. If the latest rows only have
    run_id NULL, fall back to rows exactly at max(evaluated_at).
    """
    latest_ts = (
        select(
            DbEval.repo,
            DbEval.source,
            func.max(_EVALUATED_AT).label("latest_at"),
        )
        .group_by(DbEval.repo, DbEval.source)
        .subquery()
    )

    latest_run_ids = (
        select(
            DbEval.repo,
            DbEval.source,
            DbEval.run_id,
        )
        .join(
            latest_ts,
            (DbEval.repo == latest_ts.c.repo)
            & (DbEval.source == latest_ts.c.source)
            & (_EVALUATED_AT == latest_ts.c.latest_at),
        )
        .where(DbEval.run_id.isnot(None))
        .distinct()
        .subquery()
    )

    has_run_id_at_latest = (
        select(latest_run_ids.c.repo, latest_run_ids.c.source).distinct().subquery()
    )

    eligible_by_run = select(DbEval.id).join(
        latest_run_ids,
        (DbEval.repo == latest_run_ids.c.repo)
        & (DbEval.source == latest_run_ids.c.source)
        & (DbEval.run_id == latest_run_ids.c.run_id),
    )

    eligible_by_ts = (
        select(DbEval.id)
        .join(
            latest_ts,
            (DbEval.repo == latest_ts.c.repo)
            & (DbEval.source == latest_ts.c.source)
            & (_EVALUATED_AT == latest_ts.c.latest_at),
        )
        .outerjoin(
            has_run_id_at_latest,
            (DbEval.repo == has_run_id_at_latest.c.repo)
            & (DbEval.source == has_run_id_at_latest.c.source),
        )
        .where(has_run_id_at_latest.c.repo.is_(None))
    )

    return union(eligible_by_run, eligible_by_ts)


def _csv_filter(raw: str | None) -> list[str] | None:
    """Parse a comma-separated query value into a non-empty list of trims.

    Returns ``None`` when the input is ``None`` or contains only blank
    entries, so callers can skip the filter entirely. A single value
    like ``"foo"`` returns ``["foo"]`` so the same call site can use
    ``IN(...)`` for both single- and multi-value forms.

    Trims whitespace around each value and drops blanks so a stray comma
    like ``"WARN,"`` doesn't produce a phantom empty-string clause.
    """
    if raw is None:
        return None
    parts = [s.strip() for s in raw.split(",") if s.strip()]
    return parts or None


@router.get(
    "/evaluations",
    response_model=Envelope[list[PipelineEvaluationItem]],
    summary="List evaluation findings",
    description="List pipeline evaluation findings with optional filtering.",
)
async def list_evaluations(
    repo: Annotated[str | None, Query()] = None,
    dimension: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    run_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[PipelineEvaluationItem]]:
    """Return evaluation findings with optional filters and pagination.

    The ``repo``, ``dimension``, ``severity``, and ``source`` query params
    accept a single value (``?severity=WARN``) or a comma-separated list
    (``?severity=WARN,ERROR``). The list form translates to a SQL
    ``IN(...)`` clause; the single form remains a normal equality match
    via ``IN`` with one element. Whitespace around values is trimmed.

    Closes EVAL-003's source filter: the conformance check fetches with
    ``?source=conformance_llm,conformance_deterministic,...`` and expects
    SQL ``IN`` semantics. The previous single-equality match would treat
    the whole comma-string as a single literal ``source`` value and
    return zero rows, silently breaking the check.
    """
    settings = get_settings()

    repo_filter = _csv_filter(repo)
    dimension_filter = _csv_filter(dimension)
    severity_filter = _csv_filter(severity)
    source_filter = _csv_filter(source)

    eligible = _eligible_latest_evaluation_ids_subquery()
    # Order by COALESCE(evaluated_at, created_at) DESC with id DESC as a
    # deterministic tie-breaker. Without the id tie-breaker, rows sharing a
    # timestamp came back in an unspecified order, which is what caused the
    # homepage badge to surface a stale flow_inline finding instead of the
    # most recent one.
    stmt = (
        select(DbEval)
        .where(DbEval.id.in_(eligible))
        .order_by(_EVALUATED_AT.desc(), DbEval.id.desc())
    )
    if repo_filter:
        stmt = stmt.where(DbEval.repo.in_(repo_filter))
    if dimension_filter:
        stmt = stmt.where(DbEval.dimension.in_(dimension_filter))
    if severity_filter:
        stmt = stmt.where(DbEval.severity.in_(severity_filter))
    if source_filter:
        stmt = stmt.where(DbEval.source.in_(source_filter))
    if run_id is not None:
        stmt = stmt.where(DbEval.run_id == run_id)

    total_stmt = select(func.count()).select_from(DbEval).where(DbEval.id.in_(eligible))
    if repo_filter:
        total_stmt = total_stmt.where(DbEval.repo.in_(repo_filter))
    if dimension_filter:
        total_stmt = total_stmt.where(DbEval.dimension.in_(dimension_filter))
    if severity_filter:
        total_stmt = total_stmt.where(DbEval.severity.in_(severity_filter))
    if source_filter:
        total_stmt = total_stmt.where(DbEval.source.in_(source_filter))
    if run_id is not None:
        total_stmt = total_stmt.where(DbEval.run_id == run_id)
    total = (await session.execute(total_stmt)).scalar_one()

    stmt = stmt.limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()

    data = [
        PipelineEvaluationItem(
            id=row.id,
            run_id=row.run_id,
            violation_id=row.violation_id,
            repo=row.repo,
            dimension=row.dimension,
            severity=row.severity,
            finding=row.finding or "",
            suggestion=row.suggestion,
            standards_version=row.standards_version,
            evaluator_version=row.evaluator_version,
            source=row.source,
            flow_name=row.flow_name,
            evaluated_at=row.evaluated_at or row.created_at,
        )
        for row in rows
    ]

    return success_envelope(
        data, count=len(data), total=total, version=settings.API_VERSION
    )


@router.get(
    "/evaluations/summary",
    response_model=Envelope[list[EvaluationSummaryItem]],
    summary="Evaluation summary",
    description="Aggregate evaluation findings grouped by dimension.",
)
async def evaluations_summary(
    run_id: Annotated[str | None, Query()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[EvaluationSummaryItem]]:
    """Return aggregate evaluation counts grouped by dimension."""
    settings = get_settings()

    eligible = _eligible_latest_evaluation_ids_subquery()
    stmt = (
        select(
            DbEval.dimension,
            func.sum(case((DbEval.severity == "ERROR", 1), else_=0)).label(
                "error_count"
            ),
            func.sum(case((DbEval.severity == "WARN", 1), else_=0)).label("warn_count"),
            func.sum(case((DbEval.severity == "INFO", 1), else_=0)).label("info_count"),
            func.max(_EVALUATED_AT).label("most_recent"),
        )
        .where(DbEval.id.in_(eligible))
        .group_by(DbEval.dimension)
        .order_by(func.max(_EVALUATED_AT).desc())
    )
    if run_id is not None:
        stmt = stmt.where(DbEval.run_id == run_id)
    rows = (await session.execute(stmt)).all()

    data = [
        EvaluationSummaryItem(
            dimension=d,
            error_count=error_count,
            warn_count=warn_count,
            info_count=info_count,
            most_recent=most_recent,
        )
        for d, error_count, warn_count, info_count, most_recent in rows
    ]
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


def _write_result(
    row: DbEval, *, deduplicated: bool, version: str
) -> Envelope[PipelineEvaluationWriteResult]:
    """One stored finding, and whether this request is what stored it."""
    return success_envelope(
        PipelineEvaluationWriteResult(
            id=row.id,
            run_id=row.run_id,
            violation_id=row.violation_id,
            repo=row.repo,
            dimension=row.dimension,
            severity=row.severity,
            finding=row.finding or "",
            suggestion=row.suggestion,
            standards_version=row.standards_version,
            evaluator_version=row.evaluator_version,
            source=row.source,
            flow_name=row.flow_name,
            evaluated_at=row.evaluated_at,
            deduplicated=deduplicated,
        ),
        count=1,
        total=1,
        version=version,
    )


@router.post(
    "/evaluations",
    response_model=Envelope[PipelineEvaluationWriteResult],
    summary="Write evaluation findings",
    description="Write pipeline evaluation findings. Protected (owner-based placeholder auth).",
)
async def create_evaluation(
    payload: PipelineEvaluationCreate,
    owner_id_principal: Principal = Depends(
        require_scope("pipeline.evaluations.write")
    ),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[PipelineEvaluationWriteResult]:
    """Store one finding, or recognise one this run already holds (PIPE-002).

    Idempotent on ``(run_id, repo, fingerprint)``. A redelivered queue
    message, a retried POST from the shared release workflow and a rerun of
    a job that already finished all offer a finding the run already has, and
    storing it twice puts two identical rows in Pipeline Health — which
    EVAL-003 then grades as a data-quality fault against the evaluator.

    **A suppressed write answers 200 with ``deduplicated: true``, and the
    caller is expected to read it.** A bare 200 would be the more obvious
    thing and would be wrong: the evaluator counts a 2xx as a delivered
    finding, so dropping the row silently would have it report findings as
    posted that were never stored — the same shape as the September runs
    that reported 162 posted and stored none, arrived at by a new route.

    Rows with no ``run_id`` are outside the index and are always stored.
    They belong to no run, so there is nothing for them to be idempotent
    with respect to.
    """
    owner_id = owner_id_principal.subject
    settings = get_settings()

    fingerprint = evaluation_fingerprint(
        violation_id=payload.violation_id,
        dimension=payload.dimension,
        severity=payload.severity,
        finding=payload.finding,
        suggestion=payload.suggestion,
    )

    async def already_stored() -> DbEval | None:
        if payload.run_id is None:
            return None
        result = await session.execute(
            select(DbEval).where(
                DbEval.run_id == payload.run_id,
                DbEval.repo == payload.repo,
                DbEval.fingerprint == fingerprint,
            )
        )
        return result.scalars().first()

    existing = await already_stored()
    if existing is not None:
        return _write_result(existing, deduplicated=True, version=settings.API_VERSION)

    # Legacy catch-all field intentionally left empty.
    details = None

    row = DbEval(
        owner_id=owner_id,
        repo=payload.repo,
        dimension=payload.dimension,
        severity=payload.severity,
        details=details,
        run_id=payload.run_id,
        violation_id=payload.violation_id,
        finding=payload.finding,
        suggestion=payload.suggestion,
        standards_version=payload.standards_version,
        evaluator_version=payload.evaluator_version,
        source=payload.source,
        flow_name=payload.flow_name,
        fingerprint=fingerprint,
    )
    session.add(row)
    try:
        await session.flush()
        await session.commit()
    except IntegrityError:
        # The read above is the fast path, not the guarantee. Two workers
        # draining the same queue can both miss and both insert; the unique
        # index is what makes only one of them land, and this is the other
        # one finding out. Re-raise if the conflict was something else —
        # a severity outside the 015 constraint, say — because that is a
        # bad write and not a duplicate.
        await session.rollback()
        existing = await already_stored()
        if existing is None:
            raise
        return _write_result(existing, deduplicated=True, version=settings.API_VERSION)

    await session.refresh(row)
    return _write_result(row, deduplicated=False, version=settings.API_VERSION)


@router.post(
    "/evaluations/runs",
    response_model=Envelope[EvaluationRunAccepted],
    status_code=202,
    summary="Ask for a repository to be evaluated",
    description=(
        "Hands one repository to the evaluator and acknowledges. The "
        "evaluation runs afterwards; findings arrive on /v1/evaluations."
    ),
)
async def create_evaluation_run(
    payload: EvaluationRunRequest,
    principal: Principal = Depends(require_scope("evaluations.runs.create")),
) -> Envelope[EvaluationRunAccepted]:
    """Accept one evaluation request and hand it to the evaluator.

    The caller is a release job, and its contract is fire-and-forget: it
    posts, reads the acknowledgement, and its runner shuts down. So the
    interesting question is not what this returns but whether the job
    landed — a job that never reaches the queue produces no findings, no
    failures, and a repository whose conformance record stops where it was
    while looking healthy.

    The enqueue is therefore awaited rather than backgrounded. SQS
    acknowledges in milliseconds, so waiting for it makes a dropped job
    visible while the caller is still on the line. A failure is a 502 here
    *and* a message in the errors channel, because the caller will not read
    the 502.

    Once the message is on the queue the job is durable: retried on
    failure, dead-lettered when it cannot be processed. That is the whole
    point of the change — the window between "accepted" and "done" used to
    be one process's memory, and a deploy or an OOM emptied it silently.
    """
    settings = get_settings()
    job = evaluation_dispatch.EvaluationJob(
        repo=payload.repo,
        ref=payload.ref,
        org=payload.org,
        mode=payload.mode,
        repo_id=payload.repo_id,
        run_id=payload.run_id,
    )

    try:
        accepted = await evaluation_dispatch.dispatch_evaluation(job, settings=settings)
    except evaluation_dispatch.DispatchError as exc:
        raise api_error(
            502,
            "dispatch_failed",
            f"The evaluation was not enqueued: {exc}",
        ) from exc

    data = EvaluationRunAccepted(
        run_id=payload.run_id or "",
        message_id=str(accepted.get("message_id") or ""),
        repo=payload.repo,
        ref=payload.ref,
        mode=payload.mode,
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)


def _mint_introspection_run_id(standards_version: str) -> str:
    """Its own prefix, not a fleet pass's id.

    These findings are filed against ecosystem-standards rather than any
    repository, and sharing a pass's run would put them inside a run whose
    subject is every repository but this one.
    """
    return f"introspection-{standards_version or 'unpinned'}-{uuid.uuid4().hex[:12]}"


def _mint_fleet_run_id(mode: str, standards_version: str) -> str:
    """One run id for the whole pass, in the evaluator's own format.

    Deliberately identical to ``_build_deterministic_run_id`` and
    ``_build_conformance_run_id`` in evaluator-cog: ``<prefix>-<catalog
    version>-<12 hex>``. The website filters findings by run, so a fleet
    pass whose repositories carried different ids would show as a dozen
    unrelated runs rather than one pass.

    The suffix is random rather than a timestamp, and that is not a
    stylistic choice — a timestamp resolved to the second is not unique
    between two releases that land together, which the release trigger
    makes ordinary, and two passes sharing a run id merge their findings.

    Minted here rather than in the evaluator because the evaluator no
    longer sees the pass — it sees N independent repositories, and none of
    them is in a position to name the thing they belong to.
    """
    prefix = "conformance" if mode == "llm" else "deterministic"
    suffix = uuid.uuid4().hex[:12]
    return f"{prefix}-{standards_version or 'unpinned'}-{suffix}"


async def _previous_fleet_run(session: AsyncSession) -> str:
    """The most recent fleet pass that has already been graded.

    XSTACK-008 reports which registered repositories did not resolve, and
    it reads that from the rows a pass wrote. Handing it the pass being
    dispatched right now would be a race it always loses: the
    introspection job is small and the repository jobs each clone a
    repository, so under a Lambda consumer it finishes long before the
    rows it is meant to read exist. It would report nothing, and "no
    registry entry failed to resolve" is precisely the false clean the
    check exists to prevent.

    So it grades the previous pass. A registry entry that broke today is
    flagged on tomorrow's release rather than this one — a day of latency
    on a rule about registry drift, which is the cheap half of the trade.
    The expensive half would be a completion barrier: distributed state in
    the API, and a single stuck message meaning the checks never run.

    "A fleet pass" is a run that covered more than one repository. There
    is no marker distinguishing one from a release-triggered evaluation —
    both mint ``<prefix>-<version>-<hex>`` — but only a pass spans the
    fleet, and that is visible in the rows themselves.
    """
    stmt = (
        select(DbEval.run_id)
        .where(DbEval.run_id.is_not(None))
        .group_by(DbEval.run_id)
        .having(func.count(func.distinct(DbEval.repo)) > 1)
        .order_by(func.max(DbEval.evaluated_at).desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    return str(row) if row else ""


async def _pinned_standards_version(session: AsyncSession) -> str:
    """The catalog version the whole pass grades against.

    Read once, here, rather than left to each repository's job. N jobs
    each resolving their own version means a catalog release landing
    mid-pass grades some repositories against the old rules and some
    against the new, filed under one run id that claims a single version
    for all of them — a pass that is internally inconsistent and says
    nothing about it.

    An empty string when nothing is published yet, which leaves each job
    to resolve its own as it does today. That is the honest fallback: a
    pin invented from no catalog would be worse than no pin.
    """
    stmt = select(DbCatalog).order_by(DbCatalog.version_sort.desc()).limit(1)
    row = (await session.execute(stmt)).scalars().first()
    return str(row.version) if row is not None else ""


@router.post(
    "/evaluations/introspection",
    response_model=Envelope[EvaluationIntrospectionAccepted],
    status_code=202,
    summary="Run the checks that are scoped to no repository",
    description=(
        "EVAL-003, MONO-003, XSTACK-006, XSTACK-007, XSTACK-008 and EVAL-007 "
        "grade the inventory, the stored findings and the catalog itself. "
        "Name a fan-out pass in `pass_run_id` and XSTACK-008 reports which of "
        "its registered repositories did not resolve."
    ),
)
async def create_evaluation_introspection(
    payload: EvaluationIntrospectionRequest,
    principal: Principal = Depends(require_scope("evaluations.runs.create")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[EvaluationIntrospectionAccepted]:
    """Enqueue the fleet-scoped checks and acknowledge.

    Same scope as every other evaluation request, for the reason the runs
    route already gives: every repository's CI authenticates with the one
    machine key, so a scope only this could use would be held by every
    caller that can already ask for its own evaluation.
    """
    settings = get_settings()

    standards_version = await _pinned_standards_version(session)
    # Its own prefix. These findings are filed against ecosystem-standards
    # rather than any repository, and sharing a fleet pass's id would put
    # them inside a run whose subject is every repository but this.
    run_id = payload.run_id or _mint_introspection_run_id(standards_version)

    job = evaluation_dispatch.IntrospectionJob(
        run_id=run_id,
        pass_run_id=payload.pass_run_id or "",
        standards_version=standards_version,
    )

    try:
        accepted = await evaluation_dispatch.dispatch_introspection(
            job, settings=settings
        )
    except evaluation_dispatch.DispatchError as exc:
        raise api_error(
            502,
            "dispatch_failed",
            f"The introspection pass was not enqueued: {exc}",
        ) from exc

    data = EvaluationIntrospectionAccepted(
        run_id=run_id,
        pass_run_id=payload.pass_run_id or "",
        message_id=str(accepted.get("message_id") or ""),
        standards_version=standards_version,
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/evaluations/fleet",
    response_model=Envelope[EvaluationFleetAccepted],
    status_code=202,
    summary="Ask for every repository to be evaluated, one job each",
    description=(
        "Fans the fleet out to one queue message per repository. Same intent "
        "as the retired sweep and a different mechanism: a failure retries one "
        "repository rather than the whole pass, and the work runs as wide as "
        "the consumers allow rather than serially inside one message."
    ),
)
async def create_evaluation_fleet(
    payload: EvaluationFleetRequest,
    principal: Principal = Depends(require_scope("evaluations.runs.create")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[EvaluationFleetAccepted]:
    """Fan out a whole-fleet pass and acknowledge what landed.

    Same scope as a single run, matching every other evaluation request
    and for the same reason: every repository's CI authenticates with the one machine
    key, so a scope only a fleet pass could use would be held by every
    caller that can already ask for its own evaluation.
    """
    settings = get_settings()

    standards_version = await _pinned_standards_version(session)
    run_id = payload.run_id or _mint_fleet_run_id(payload.mode, standards_version)

    job = evaluation_dispatch.FleetJob(
        mode=payload.mode,
        run_id=run_id,
        standards_version=standards_version,
    )

    try:
        accepted = await evaluation_dispatch.dispatch_fleet(job, settings=settings)
    except evaluation_dispatch.DispatchError as exc:
        raise api_error(
            502,
            "dispatch_failed",
            f"The fleet pass was not enqueued: {exc}",
        ) from exc

    # The checks that belong to no repository, asked for as part of the
    # pass rather than by the caller. EVAL-003, MONO-003, XSTACK-006,
    # XSTACK-007, XSTACK-008 and EVAL-007 grade the inventory, the stored
    # findings and the catalog itself, so no repository job carries them —
    # and a caller that has to remember a second request is a caller that
    # eventually forgets, leaving six checks silently not running.
    #
    # Its own run id, and the previous pass to grade. See
    # _previous_fleet_run for why not this one.
    introspection_run_id = _mint_introspection_run_id(standards_version)
    try:
        await evaluation_dispatch.dispatch_introspection(
            evaluation_dispatch.IntrospectionJob(
                run_id=introspection_run_id,
                pass_run_id=await _previous_fleet_run(session),
                standards_version=standards_version,
            ),
            settings=settings,
        )
    except evaluation_dispatch.DispatchError as exc:
        # Not fatal to the pass. Fifteen repositories are already being
        # evaluated and saying the whole request failed would be false.
        # _enqueue has already reported it to the errors channel; this
        # puts it in the answer too, so the caller is not told a pass is
        # whole when part of it is missing.
        logger.warning("fleet pass %s: introspection not enqueued: %s", run_id, exc)
        introspection_run_id = ""

    data = EvaluationFleetAccepted(
        # The id minted above, not one read back out of the dispatcher.
        # It is the same value — the job carried it down — and asking for
        # it again only creates a way for the acknowledgement and the
        # messages to disagree about what the pass is called.
        run_id=run_id,
        introspection_run_id=introspection_run_id,
        mode=payload.mode,
        standards_version=standards_version,
        enqueued=[
            EvaluationFleetRepo(repo=row["repo"], message_id=row["message_id"])
            for row in accepted.get("enqueued", [])
        ],
        failed=list(accepted.get("failed", [])),
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)
