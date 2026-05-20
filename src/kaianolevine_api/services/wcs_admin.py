"""Admin writes for WCS input-layer corrections and additions."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    WcsAttributionAddition,
    WcsAttributionCorrection,
    WcsDrillPurposeAddition,
    WcsEntityRelationAddition,
    WcsNameCorrection,
    WcsSource,
    WcsSourceExtraction,
    WcsSourceMetadataCorrection,
    WcsTechniqueRequirementAddition,
)
from ..schemas import (
    WcsAttributionAdditionCreate,
    WcsAttributionCorrectionCreate,
    WcsDrillPurposeAdditionCreate,
    WcsEntityRelationAdditionCreate,
    WcsNameCorrectionCreate,
    WcsSourceMetadataCorrectionCreate,
    WcsTechniqueRequirementAdditionCreate,
)
from .wcs_composition import CompositionResult, compose_source


async def create_name_correction(
    session: AsyncSession,
    owner_id: str,
    payload: WcsNameCorrectionCreate,
) -> tuple[WcsNameCorrection, list[uuid.UUID], bool, str]:
    row = WcsNameCorrection(
        raw_name=payload.raw_name,
        corrected_name=payload.corrected_name,
        scope=payload.scope,
        source_id=payload.source_id,
        reason=payload.reason,
        created_by=owner_id,
    )
    session.add(row)
    await session.flush()

    if payload.scope == "global" or payload.source_id is None:
        return (
            row,
            [],
            True,
            "Global name correction saved; recompose affected sources manually.",
        )

    await compose_source(session, payload.source_id)
    return row, [payload.source_id], False, ""


async def create_attribution_correction(
    session: AsyncSession,
    owner_id: str,
    payload: WcsAttributionCorrectionCreate,
) -> tuple[WcsAttributionCorrection, list[uuid.UUID]]:
    row = WcsAttributionCorrection(
        source_id=payload.source_id,
        attribution_target=payload.attribution_target,
        field=payload.field,
        corrected_value=payload.corrected_value,
        reason=payload.reason,
        created_by=owner_id,
    )
    session.add(row)
    await session.flush()
    await compose_source(session, payload.source_id)
    return row, [payload.source_id]


async def create_metadata_correction(
    session: AsyncSession,
    owner_id: str,
    payload: WcsSourceMetadataCorrectionCreate,
) -> tuple[WcsSourceMetadataCorrection, list[uuid.UUID]]:
    row = WcsSourceMetadataCorrection(
        source_id=payload.source_id,
        field=payload.field,
        corrected_value=payload.corrected_value,
        reason=payload.reason,
        created_by=owner_id,
    )
    session.add(row)
    await session.flush()

    source = await session.get(WcsSource, payload.source_id)
    if source is not None and payload.field in {
        "session_date",
        "session_type",
        "organization",
        "instructors",
        "students",
        "title",
        "visibility",
        "is_default_visible",
    }:
        val = payload.corrected_value
        if payload.field == "session_date" and isinstance(val, str):
            source.session_date = dt.date.fromisoformat(val)
        elif payload.field == "session_type" and isinstance(val, str):
            source.session_type = val
        elif payload.field == "organization" and isinstance(val, str):
            source.organization = val
        elif payload.field == "title" and isinstance(val, str):
            source.title = val
        elif payload.field == "visibility" and isinstance(val, str):
            source.visibility = val
        elif payload.field == "is_default_visible" and isinstance(val, bool):
            source.is_default_visible = val
        elif payload.field == "instructors" and isinstance(val, list):
            source.instructors_raw = [str(x) for x in val]
        elif payload.field == "students" and isinstance(val, list):
            source.students_raw = [str(x) for x in val]

    await compose_source(session, payload.source_id)
    return row, [payload.source_id]


async def create_attribution_addition(
    session: AsyncSession,
    owner_id: str,
    payload: WcsAttributionAdditionCreate,
) -> tuple[WcsAttributionAddition, list[uuid.UUID]]:
    row = WcsAttributionAddition(
        source_id=payload.source_id,
        entity_slug=payload.entity_slug,
        instructor_slug=payload.instructor_slug,
        attribution_kind=payload.attribution_kind,
        prose=payload.prose,
        reason=payload.reason,
        created_by=owner_id,
    )
    session.add(row)
    await session.flush()
    recomposed: list[uuid.UUID] = []
    if payload.source_id is not None:
        await compose_source(session, payload.source_id)
        recomposed.append(payload.source_id)
    return row, recomposed


async def create_drill_purpose_addition(
    session: AsyncSession,
    owner_id: str,
    payload: WcsDrillPurposeAdditionCreate,
) -> tuple[WcsDrillPurposeAddition, list[uuid.UUID]]:
    row = WcsDrillPurposeAddition(
        drill_entity_slug=payload.drill_entity_slug,
        source_id=payload.source_id,
        skill_name=payload.skill_name,
        prose=payload.prose,
        focus_context=payload.focus_context,
        reason=payload.reason,
        created_by=owner_id,
    )
    session.add(row)
    await session.flush()
    recomposed: list[uuid.UUID] = []
    if payload.source_id is not None:
        await compose_source(session, payload.source_id)
        recomposed.append(payload.source_id)
    return row, recomposed


async def create_technique_requirement_addition(
    session: AsyncSession,
    owner_id: str,
    payload: WcsTechniqueRequirementAdditionCreate,
) -> tuple[WcsTechniqueRequirementAddition, list[uuid.UUID]]:
    row = WcsTechniqueRequirementAddition(
        technique_entity_slug=payload.technique_entity_slug,
        source_id=payload.source_id,
        skill_name=payload.skill_name,
        prose=payload.prose,
        reason=payload.reason,
        created_by=owner_id,
    )
    session.add(row)
    await session.flush()
    recomposed: list[uuid.UUID] = []
    if payload.source_id is not None:
        await compose_source(session, payload.source_id)
        recomposed.append(payload.source_id)
    return row, recomposed


async def create_entity_relation_addition(
    session: AsyncSession,
    owner_id: str,
    payload: WcsEntityRelationAdditionCreate,
) -> tuple[WcsEntityRelationAddition, list[uuid.UUID]]:
    row = WcsEntityRelationAddition(
        from_entity_slug=payload.from_entity_slug,
        to_entity_slug=payload.to_entity_slug,
        relation_kind=payload.relation_kind,
        prose=payload.prose,
        reason=payload.reason,
        created_by=owner_id,
    )
    session.add(row)
    await session.flush()
    source_ids = list(
        (
            await session.execute(
                select(WcsSourceExtraction.source_id).where(
                    WcsSourceExtraction.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    for sid in source_ids:
        await compose_source(session, sid)
    return row, source_ids


async def recompose_source(
    session: AsyncSession,
    source_id: uuid.UUID,
) -> CompositionResult | None:
    source = await session.get(WcsSource, source_id)
    if source is None:
        return None
    return await compose_source(session, source_id)
