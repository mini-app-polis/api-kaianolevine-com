"""WCS admin router — input-layer corrections, additions, recompose, gaps."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_START, LOG_SUCCESS
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_wcs_admin
from ..config import get_settings
from ..database import get_db_session
from ..schemas import (
    Envelope,
    WcsAdminCorrectionResult,
    WcsAttributionAdditionCreate,
    WcsAttributionCorrectionCreate,
    WcsDrillPurposeAdditionCreate,
    WcsEntityRelationAdditionCreate,
    WcsGapItem,
    WcsNameCorrectionCreate,
    WcsRecomposeResult,
    WcsSourceMetadataCorrectionCreate,
    WcsTechniqueRequirementAdditionCreate,
    api_error,
    success_envelope,
)
from ..services import wcs_admin as admin_svc
from ..services import wcs_wiki as wiki_svc

router = APIRouter()
log = logger_mod.get_logger()


@router.post(
    "/wcs/admin/corrections/name",
    response_model=Envelope[WcsAdminCorrectionResult],
    summary="Create a name correction",
    description="Writes wcs_name_corrections. Global corrections defer auto-recompose.",
)
async def create_name_correction(
    payload: WcsNameCorrectionCreate,
    owner_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsAdminCorrectionResult]:
    """Record a name correction and recompose affected sources (deferred when global)."""
    log.info("%s name correction raw=%s", LOG_START, payload.raw_name)
    settings = get_settings()
    row, recomposed, deferred, message = await admin_svc.create_name_correction(
        session, owner_id, payload
    )
    await session.commit()
    result = WcsAdminCorrectionResult(
        id=row.id,
        field="name",
        recomposed_source_ids=recomposed,
        deferred=deferred,
        message=message,
    )
    log.info("%s name correction id=%s", LOG_SUCCESS, row.id)
    return success_envelope(result, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/wcs/admin/corrections/attribution",
    response_model=Envelope[WcsAdminCorrectionResult],
    summary="Create an attribution correction",
    description=(
        "Records an admin-driven correction to a single attribution field on a "
        "specific source (prose, position, raw_term, ...) and triggers immediate "
        "recomposition of that source."
    ),
)
async def create_attribution_correction(
    payload: WcsAttributionCorrectionCreate,
    owner_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsAdminCorrectionResult]:
    """Record an attribution correction and recompose the affected source."""
    settings = get_settings()
    row, recomposed = await admin_svc.create_attribution_correction(
        session, owner_id, payload
    )
    await session.commit()
    result = WcsAdminCorrectionResult(
        id=row.id,
        field=payload.field,
        recomposed_source_ids=recomposed,
    )
    return success_envelope(result, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/wcs/admin/corrections/metadata",
    response_model=Envelope[WcsAdminCorrectionResult],
    summary="Create a source metadata correction",
    description=(
        "Corrects a source-level metadata field (title, organization, session_date, "
        "session_type, etc.) and recomposes the source so downstream views pick up "
        "the change."
    ),
)
async def create_metadata_correction(
    payload: WcsSourceMetadataCorrectionCreate,
    owner_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsAdminCorrectionResult]:
    """Record a source metadata correction and recompose the affected source."""
    settings = get_settings()
    row, recomposed = await admin_svc.create_metadata_correction(
        session, owner_id, payload
    )
    await session.commit()
    result = WcsAdminCorrectionResult(
        id=row.id,
        field=payload.field,
        recomposed_source_ids=recomposed,
    )
    return success_envelope(result, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/wcs/admin/additions/attribution",
    response_model=Envelope[WcsAdminCorrectionResult],
    summary="Create an attribution addition",
    description=(
        "Adds a brand-new attribution row to an existing source (e.g., binding an "
        "entity to the source with admin-authored prose) and recomposes the source."
    ),
)
async def create_attribution_addition(
    payload: WcsAttributionAdditionCreate,
    owner_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsAdminCorrectionResult]:
    """Append an admin-authored attribution to a source and recompose."""
    settings = get_settings()
    row, recomposed = await admin_svc.create_attribution_addition(
        session, owner_id, payload
    )
    await session.commit()
    result = WcsAdminCorrectionResult(
        id=row.id,
        recomposed_source_ids=recomposed,
    )
    return success_envelope(result, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/wcs/admin/additions/drill_purpose",
    response_model=Envelope[WcsAdminCorrectionResult],
    summary="Create a drill purpose addition",
    description=(
        "Adds an admin-authored drill→purpose link to a source, attaching a goal "
        "or rationale that the upstream extraction missed, and recomposes the source."
    ),
)
async def create_drill_purpose_addition(
    payload: WcsDrillPurposeAdditionCreate,
    owner_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsAdminCorrectionResult]:
    """Append an admin-authored drill-purpose pairing to a source and recompose."""
    settings = get_settings()
    row, recomposed = await admin_svc.create_drill_purpose_addition(
        session, owner_id, payload
    )
    await session.commit()
    result = WcsAdminCorrectionResult(
        id=row.id,
        recomposed_source_ids=recomposed,
    )
    return success_envelope(result, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/wcs/admin/additions/technique_requirement",
    response_model=Envelope[WcsAdminCorrectionResult],
    summary="Create a technique requirement addition",
    description=(
        "Adds an admin-authored technique→requirement edge (a concept or technique "
        "that the technique requires) to a source and recomposes the source."
    ),
)
async def create_technique_requirement_addition(
    payload: WcsTechniqueRequirementAdditionCreate,
    owner_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsAdminCorrectionResult]:
    """Append an admin-authored technique requirement to a source and recompose."""
    settings = get_settings()
    row, recomposed = await admin_svc.create_technique_requirement_addition(
        session, owner_id, payload
    )
    await session.commit()
    result = WcsAdminCorrectionResult(
        id=row.id,
        recomposed_source_ids=recomposed,
    )
    return success_envelope(result, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/wcs/admin/additions/entity_relation",
    response_model=Envelope[WcsAdminCorrectionResult],
    summary="Create an entity relation addition",
    description=(
        "Adds an admin-authored relation between two entities (e.g., concept→concept "
        "or technique→concept) attributed to a source, and recomposes the source."
    ),
)
async def create_entity_relation_addition(
    payload: WcsEntityRelationAdditionCreate,
    owner_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsAdminCorrectionResult]:
    """Append an admin-authored entity-to-entity relation to a source and recompose."""
    settings = get_settings()
    row, recomposed = await admin_svc.create_entity_relation_addition(
        session, owner_id, payload
    )
    await session.commit()
    result = WcsAdminCorrectionResult(
        id=row.id,
        recomposed_source_ids=recomposed,
    )
    return success_envelope(result, count=1, total=1, version=settings.API_VERSION)


@router.post(
    "/wcs/admin/recompose/{source_id}",
    response_model=Envelope[WcsRecomposeResult],
    summary="Manually re-run composition for a source",
    description=(
        "Forces a recompose pass over the given source: re-derives its attributions, "
        "definitions, relations, drill purposes, technique requirements, and "
        "references. Used after a global correction has been issued or to fix a "
        "drifted composition."
    ),
)
async def recompose_source(
    source_id: uuid.UUID,
    _admin_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsRecomposeResult]:
    """Re-run composition for the given source and return the row counts written."""
    log.info("%s recompose source_id=%s", LOG_START, source_id)
    settings = get_settings()
    composition = await admin_svc.recompose_source(session, source_id)
    if composition is None:
        raise api_error(404, "source_not_found", "Source not found")
    await session.commit()
    result = WcsRecomposeResult(
        source_id=source_id,
        attributions_written=composition.attributions_written,
        definitions_written=composition.definitions_written,
        relations_written=composition.relations_written,
        drill_purposes_written=composition.drill_purposes_written,
        technique_requirements_written=composition.technique_requirements_written,
        references_written=composition.references_written,
    )
    log.info("%s recompose done source_id=%s", LOG_SUCCESS, source_id)
    return success_envelope(result, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/wcs/admin/gaps/orphan-entities",
    response_model=Envelope[list[WcsGapItem]],
    summary="List entities with no attributions",
    description=(
        "Returns entities (concepts / techniques / patterns / drills) that exist in "
        "the substrate but have zero attribution rows pointing at them — useful for "
        "spotting upstream extraction drift."
    ),
)
async def gaps_orphan_entities(
    _admin_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsGapItem]]:
    """List entities with no attribution rows pointing at them."""
    settings = get_settings()
    rows = await wiki_svc.list_orphan_entities(session)
    data = [
        WcsGapItem(slug=s, name=n, kind=k, count=0, detail="no attributions")
        for s, n, k in rows
    ]
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


@router.get(
    "/wcs/admin/gaps/stub-entities",
    response_model=Envelope[list[WcsGapItem]],
    summary="List stub or under-attributed entities",
    description=(
        "Returns entities marked as stubs or with fewer than two attributions — the "
        "queue of pages that need either more source coverage or a manual merge."
    ),
)
async def gaps_stub_entities(
    _admin_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsGapItem]]:
    """List entities flagged as stubs or with fewer than two attributions."""
    settings = get_settings()
    rows = await wiki_svc.list_stub_entities(session)
    data = [
        WcsGapItem(
            slug=s,
            name=n,
            kind=k,
            count=cnt,
            detail="stub status or fewer than 2 attributions",
        )
        for s, n, k, cnt in rows
    ]
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


@router.get(
    "/wcs/admin/gaps/skills-unpaired",
    response_model=Envelope[list[WcsGapItem]],
    summary="List skill slugs not paired across drill/technique tables",
    description=(
        "Returns skill slugs that appear on one side of the drill/technique split but "
        "lack the matching counterpart — surfaces alias-map and slug-collapse work."
    ),
)
async def gaps_skills_unpaired(
    _admin_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsGapItem]]:
    """List skill slugs that appear on only one side of the drill/technique split."""
    settings = get_settings()
    rows = await wiki_svc.list_unpaired_skill_slugs(session)
    data = [
        WcsGapItem(slug=s, name="", kind="skill", count=0, detail=detail)
        for s, detail in rows
    ]
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


@router.get(
    "/wcs/admin/gaps/sources-uncomposed",
    response_model=Envelope[list[WcsGapItem]],
    summary="List sources with active extraction but no attributions",
    description=(
        "Returns sources whose upstream extraction has populated entities but for "
        "which the composition pass has never run or produced no attributions — "
        "useful for spotting recompose failures."
    ),
)
async def gaps_sources_uncomposed(
    _admin_id: str = Depends(require_wcs_admin),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsGapItem]]:
    """List sources with extraction present but no attributions written."""
    settings = get_settings()
    rows = await wiki_svc.list_uncomposed_sources(session)
    data = [
        WcsGapItem(slug=source_id, name=title, kind="source", count=0)
        for source_id, title in rows
    ]
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )
