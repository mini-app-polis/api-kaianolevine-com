from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from identity.types import Principal
from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_START, LOG_SUCCESS, LOG_WARNING
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..database import get_db_session
from ..models import Set as DbSet
from ..schemas import Envelope, IngestResponseData, IngestSet, success_envelope
from ..services.flags import is_enabled
from ..services.reconciliation import reconcile_set_tracks

router = APIRouter()
log = logger_mod.get_logger()


@router.post(
    "/ingest",
    response_model=Envelope[IngestResponseData],
    summary="Ingest set + reconcile catalog",
    description="Accept a set with tracks, reconcile, and return catalog upsert stats.",
)
async def ingest_set(
    payload: IngestSet = Body(..., embed=False),
    principal: Principal = Depends(require_scope("catalog.sets.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[IngestResponseData]:
    """Ingest one DJ set payload and update tracks plus catalog stats.

    First endpoint on the identity path: verify, resolve, authorize, audit.
    Every call writes a row to identity_audit_events naming the principal, so
    the trail records which cog ingested rather than only that a cog did.

    ``owner_id`` remains the caller's Clerk subject, unchanged in meaning. It
    does change in value when a cog moves from the shared fleet secret to its
    own — a known, accepted discontinuity: rows written before the move keep
    the old subject, so re-ingesting a source_file from before it will create
    a second set rather than update the first.
    """
    owner_id = principal.subject
    log.info(
        "%s ingest received source_file=%s principal=%s",
        LOG_START,
        payload.source_file,
        principal.display_name or principal.subject,
    )

    if not await is_enabled("flags.deejay_api.ingest_enabled", session):
        log.warning("%s ingest disabled by feature flag", LOG_WARNING)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "feature_disabled",
                "message": "Ingest is currently disabled",
            },
        )

    existing_set = None
    if payload.source_file:
        lookup = await session.execute(
            select(DbSet).where(
                DbSet.owner_id == owner_id,
                DbSet.source_file == payload.source_file,
            )
        )
        existing_set = lookup.scalars().first()

    if existing_set is not None:
        db_set = existing_set
        is_reingestion = True
    else:
        db_set = DbSet(
            owner_id=owner_id,
            set_date=payload.set_date,
            venue=payload.venue,
            source_file=payload.source_file,
        )
        session.add(db_set)
        try:
            await session.flush()
            is_reingestion = False
        except IntegrityError:
            await session.rollback()
            lookup = await session.execute(
                select(DbSet).where(
                    DbSet.owner_id == owner_id,
                    DbSet.source_file == payload.source_file,
                )
            )
            existing_set = lookup.scalars().first()
            if existing_set is None:
                raise
            db_set = existing_set
            is_reingestion = True

    if is_reingestion:
        log.info(
            "%s re-ingestion detected for source_file=%s",
            LOG_WARNING,
            payload.source_file,
        )

    result = await reconcile_set_tracks(
        session=session,
        owner_id=owner_id,
        set_id=db_set.id,
        set_date=payload.set_date,
        tracks=payload.tracks,
        is_reingestion=is_reingestion,
    )

    await session.commit()

    log.info(
        "%s ingest complete set_id=%s tracks=%s catalog_new=%s",
        LOG_SUCCESS,
        db_set.id,
        result.tracks_inserted,
        result.catalog_new,
    )

    data = IngestResponseData(
        set_id=db_set.id,
        tracks_created=result.tracks_inserted,
        catalog_new=result.catalog_new,
        catalog_updated=result.catalog_updated,
        catalog_unchanged=result.catalog_unchanged,
    )
    return success_envelope(data, count=1, total=1)
