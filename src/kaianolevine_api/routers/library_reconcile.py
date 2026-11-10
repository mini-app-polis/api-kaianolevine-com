"""The library reconcile's differences, and the owner's approvals.

deejay-cog's library reconcile compares each linked library file's owned
fields with its catalog entry (migration 102; deejay-cog ADR-0008, step 3).
Each run reports the files it compared and their differences, under
``catalog.library.write``; for those files the report replaces what is held.
The owner reviews the open differences on the WCS site and sets the
approvals, under ``catalog.tracks.write`` (the catalog-editor role): whole
kinds, or ``other`` differences one file and field at a time, each in one
of two directions. Approved for the catalog, the catalog's value is written
over the file; approved for the file, the file's value goes up to its entry
(through ``POST /v1/catalog/library/files/edited`` with ``keep_file``) and
the file is not written. Either way the two then match. The reconcile's
write runs read the approvals and do only what they cover; nothing here
writes music.

Seeding carries approvals the owner gave before the API held them (deejay-
cog's state file) and is refused once any are set, so the machine scope can
never change approvals the owner set here.
"""

from __future__ import annotations

from typing import Literal

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
from ..services.fill_up import fill_up_refused

router = APIRouter()

RECONCILE = "/catalog/library/reconcile"


def _approvals_out(row: DbApprovals | None) -> ReconcileApprovals:
    if row is None:
        return ReconcileApprovals()
    return ReconcileApprovals.model_validate(
        {
            "kinds": list(row.kinds),
            "files": dict(row.files),
            "file_wins_kinds": list(row.file_wins_kinds or []),
            "file_wins_files": dict(row.file_wins_files or {}),
            "updated_at": row.updated_at,
            "updated_by": row.updated_by,
        }
    )


def approved_for(
    approvals: ReconcileApprovals, kind: str, file_id: str, field: str
) -> Literal["catalog", "file"] | None:
    """Which value ``approvals`` let win for one difference: by its kind, or
    for ``other``, its file and field (as the reconcile's write run reads
    them). None when not approved."""
    if kind == "other":
        if field in approvals.files.get(file_id, []):
            return "catalog"
        if field in approvals.file_wins_files.get(file_id, []):
            return "file"
        return None
    if kind in approvals.kinds:
        return "catalog"
    if kind in approvals.file_wins_kinds:
        return "file"
    return None


def _stored(payload: ReconcileApprovalsSet) -> dict[str, object]:
    """The approvals as the row holds them: sorted, no file left empty."""
    return {
        "kinds": sorted(set(payload.kinds)),
        "files": {k: sorted(set(v)) for k, v in payload.files.items() if v},
        "file_wins_kinds": sorted(set(payload.file_wins_kinds)),
        "file_wins_files": {
            k: sorted(set(v)) for k, v in payload.file_wins_files.items() if v
        },
    }


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
    latest = {
        (d.drive_file_id, d.field): d
        for d in payload.differences
        if not (d.kind == "fill_up" and fill_up_refused(d.field, d.file_value))
    }
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
        .values(id=1, **_stored(payload), updated_by=f"seed:{principal.subject}")
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
        "approvals, and for which side (approved_for: catalog or file). "
        "Requires catalog.tracks.write."
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
            approved=(side := approved_for(approvals, d.kind, d.drive_file_id, d.field))
            is not None,
            approved_for=side,
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
        "Replaces the approvals, in both directions: for the catalog (kinds, "
        "files), the catalog's value is written over the file; for the file "
        "(file_wins_kinds, file_wins_files), the file's value goes up to the "
        "entry. Kinds are approved whole (never 'other'); 'other' "
        "differences by file and field. A kind, or a file's field, goes one "
        "way only. A file's entry with no fields is dropped. Requires "
        "catalog.tracks.write."
    ),
)
async def set_reconcile_approvals(
    payload: ReconcileApprovalsSet,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[ReconcileApprovals]:
    """The owner's approvals, replaced."""
    values = {
        **_stored(payload),
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
