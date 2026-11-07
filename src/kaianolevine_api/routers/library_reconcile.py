"""The library reconcile's differences, and the owner's approvals.

deejay-cog's library reconcile compares each linked library file's owned
fields with its catalog entry (migration 102; deejay-cog ADR-0008, step 3).
Each run reports the files it compared and their differences, under
``catalog.library.write``; for those files the report replaces what is held.
The owner reviews the open differences on the WCS site and sets the
approvals, under ``catalog.tracks.write`` (the catalog-editor role): whole
kinds, or ``other`` differences one file and field at a time. The
reconcile's write runs read the approvals and write only what they cover;
nothing here writes music.

Seeding carries approvals the owner gave before the API held them (deejay-
cog's state file) and is refused once any are set, so the machine scope can
never change approvals the owner set here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from identity.types import Principal
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..database import get_db_session
from ..models import LibraryFile as DbLibraryFile
from ..models import LibraryReconcileApprovals as DbApprovals
from ..models import LibraryReconcileDifference as DbDifference
from ..schemas import (
    Envelope,
    ReconcileApprovals,
    ReconcileApprovalsSet,
    ReconcileDifferenceOut,
    ReconcileReport,
    ReconcileReportResult,
    ReconcileReview,
    api_error,
    success_envelope,
)

router = APIRouter()

RECONCILE = "/catalog/library/reconcile"


def _approvals_out(row: DbApprovals | None) -> ReconcileApprovals:
    if row is None:
        return ReconcileApprovals()
    return ReconcileApprovals.model_validate(
        {
            "kinds": list(row.kinds),
            "files": dict(row.files),
            "updated_at": row.updated_at,
            "updated_by": row.updated_by,
        }
    )


def approves(
    approvals: ReconcileApprovals, kind: str, file_id: str, field: str
) -> bool:
    """Whether ``approvals`` cover one difference: its kind, or for
    ``other``, its file and field (as the reconcile's write run reads them)."""
    if kind == "other":
        return field in approvals.files.get(file_id, [])
    return kind in approvals.kinds


async def _counts(session: AsyncSession) -> ReconcileReportResult:
    total, files = (
        await session.execute(
            select(func.count(), func.count(func.distinct(DbDifference.drive_file_id)))
        )
    ).one()
    return ReconcileReportResult(
        open_differences=int(total), files_with_differences=int(files)
    )


@router.post(
    f"{RECONCILE}/differences",
    response_model=Envelope[ReconcileReportResult],
    summary="Report the files a reconcile run compared and their differences",
    description=(
        "For every file the run compared, the differences sent replace the "
        "ones held: a compared file sent with none has none left. Files the "
        "run did not compare keep theirs. Requires catalog.library.write."
    ),
)
async def report_reconcile_differences(
    payload: ReconcileReport,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[ReconcileReportResult]:
    """Store one run's differences for the files it compared."""
    del principal
    if payload.compared:
        await session.execute(
            delete(DbDifference).where(
                DbDifference.drive_file_id.in_(set(payload.compared))
            )
        )
    latest = {(d.drive_file_id, d.field): d for d in payload.differences}
    if latest:
        await session.execute(
            insert(DbDifference).values(
                [
                    {
                        "drive_file_id": d.drive_file_id,
                        "field": d.field,
                        "kind": d.kind,
                        "path": d.path,
                        "file_value": d.file_value,
                        "catalog_value": d.catalog_value,
                        "run_id": payload.run_id,
                    }
                    for d in latest.values()
                ]
            )
        )
    await session.commit()
    return success_envelope(await _counts(session), count=1, total=1)


@router.get(
    f"{RECONCILE}/approvals",
    response_model=Envelope[ReconcileApprovals],
    summary="Read the approvals the reconcile's write runs apply",
    description=(
        "Empty, with no updated_at, until any are set. Requires catalog.library.write."
    ),
)
async def read_reconcile_approvals(
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[ReconcileApprovals]:
    """The approvals, for a write run."""
    del principal
    row = await session.get(DbApprovals, 1)
    return success_envelope(_approvals_out(row), count=1, total=1)


@router.post(
    f"{RECONCILE}/approvals/seed",
    response_model=Envelope[ReconcileApprovals],
    summary="Seed the approvals given before the API held them",
    description=(
        "Sets the approvals only while none have ever been set; afterwards "
        "it is refused with 409, and only the owner changes them. Requires "
        "catalog.library.write."
    ),
)
async def seed_reconcile_approvals(
    payload: ReconcileApprovalsSet,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[ReconcileApprovals]:
    """Carry approvals over from deejay-cog's state file, once."""
    stmt = (
        insert(DbApprovals)
        .values(
            id=1,
            kinds=sorted(set(payload.kinds)),
            files={k: sorted(set(v)) for k, v in payload.files.items()},
            updated_by=f"seed:{principal.subject}",
        )
        .on_conflict_do_nothing(index_elements=[DbApprovals.id])
        .returning(DbApprovals.id)
    )
    if (await session.execute(stmt)).scalar_one_or_none() is None:
        await session.rollback()
        raise api_error(409, "conflict", "Approvals are already set")
    await session.commit()
    row = await session.get(DbApprovals, 1)
    return success_envelope(_approvals_out(row), count=1, total=1)


@router.get(
    RECONCILE,
    response_model=Envelope[ReconcileReview],
    summary="The reconcile's open differences, and the approvals",
    description=(
        "Every open difference of a file still in the library, by kind, then "
        "path and field, each marked approved or not by the current "
        "approvals. Requires catalog.tracks.write."
    ),
)
async def review_reconcile(
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[ReconcileReview]:
    """What the owner's review page shows."""
    del principal
    approvals = _approvals_out(await session.get(DbApprovals, 1))
    rows = (
        await session.execute(
            select(DbDifference, DbLibraryFile.catalog_id)
            .join(
                DbLibraryFile,
                DbLibraryFile.drive_file_id == DbDifference.drive_file_id,
            )
            .where(DbLibraryFile.present.is_(True))
            .order_by(DbDifference.kind, DbDifference.path, DbDifference.field)
        )
    ).all()
    differences = [
        ReconcileDifferenceOut(
            drive_file_id=d.drive_file_id,
            path=d.path,
            field=d.field,  # type: ignore[arg-type]
            kind=d.kind,  # type: ignore[arg-type]
            file_value=d.file_value,
            catalog_value=d.catalog_value,
            run_id=d.run_id,
            reported_at=d.reported_at,
            catalog_id=catalog_id,
            approved=approves(approvals, d.kind, d.drive_file_id, d.field),
        )
        for d, catalog_id in rows
    ]
    review = ReconcileReview(approvals=approvals, differences=differences)
    return success_envelope(review, count=len(differences), total=len(differences))


@router.put(
    f"{RECONCILE}/approvals",
    response_model=Envelope[ReconcileApprovals],
    summary="Set the approvals the reconcile's write runs apply",
    description=(
        "Replaces the approvals: the kinds approved whole (never 'other'), "
        "and 'other' differences by file and field. A file's entry with no "
        "fields is dropped. Requires catalog.tracks.write."
    ),
)
async def set_reconcile_approvals(
    payload: ReconcileApprovalsSet,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[ReconcileApprovals]:
    """The owner's approvals, replaced."""
    values = {
        "kinds": sorted(set(payload.kinds)),
        "files": {k: sorted(set(v)) for k, v in payload.files.items() if v},
        "updated_by": principal.subject,
        "updated_at": func.now(),
    }
    stmt = insert(DbApprovals).values(id=1, **values)
    await session.execute(
        stmt.on_conflict_do_update(index_elements=[DbApprovals.id], set_=values)
    )
    await session.commit()
    row = await session.get(DbApprovals, 1, populate_existing=True)
    return success_envelope(_approvals_out(row), count=1, total=1)
