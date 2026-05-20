"""Wiki read queries over the WCS entity substrate."""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    WcsDrillPurpose,
    WcsEntity,
    WcsEntityAlias,
    WcsEntityDefinition,
    WcsEntityRelation,
    WcsInstructor,
    WcsInstructorAlias,
    WcsSource,
    WcsSourceAttribution,
    WcsSourceExtraction,
    WcsSourceReference,
    WcsTechniqueRequirement,
)
from ..schemas import (
    WcsDrillPurposeItem,
    WcsEntityDefinitionItem,
    WcsEntityItem,
    WcsEntityRelationItem,
    WcsEntityViewItem,
    WcsInstructorItem,
    WcsInstructorViewItem,
    WcsSourceAttributionItem,
    WcsSourceItem,
    WcsSourceReferenceItem,
    WcsSourceViewItem,
    WcsTechniqueRequirementItem,
    WcsWikiExportItem,
)
from .wcs_source_visibility import visible_source_ids_for_user


def _entity_item(entity: WcsEntity, aliases: list[str] | None = None) -> WcsEntityItem:
    return WcsEntityItem(
        id=entity.id,
        slug=entity.slug,
        canonical_name=entity.canonical_name,
        kind=entity.kind,
        overview_md=entity.overview_md,
        status=entity.status,
        external_origin=entity.external_origin or {},
        aliases=aliases or [],
    )


def _instructor_item(
    instructor: WcsInstructor, aliases: list[str] | None = None
) -> WcsInstructorItem:
    return WcsInstructorItem(
        id=instructor.id,
        slug=instructor.slug,
        canonical_name=instructor.canonical_name,
        background_md=instructor.background_md,
        teaching_themes_md=instructor.teaching_themes_md,
        notable_framings_md=instructor.notable_framings_md,
        aliases=aliases or [],
    )


def _source_item(source: WcsSource) -> WcsSourceItem:
    return WcsSourceItem(
        id=source.id,
        transcript_id=source.transcript_id,
        title=source.title,
        session_date=source.session_date,
        session_type=source.session_type,
        instructors_raw=list(source.instructors_raw or []),
        students_raw=list(source.students_raw or []),
        organization=source.organization,
        visibility=source.visibility,
        is_default_visible=source.is_default_visible,
        created_at=source.created_at,
    )


async def _alias_map_for_entities(
    session: AsyncSession, entity_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not entity_ids:
        return {}
    rows = await session.execute(
        select(WcsEntityAlias.entity_id, WcsEntityAlias.alias).where(
            WcsEntityAlias.entity_id.in_(entity_ids)
        )
    )
    out: dict[uuid.UUID, list[str]] = defaultdict(list)
    for entity_id, alias in rows.all():
        out[entity_id].append(alias)
    return dict(out)


async def _alias_map_for_instructors(
    session: AsyncSession, instructor_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not instructor_ids:
        return {}
    rows = await session.execute(
        select(WcsInstructorAlias.instructor_id, WcsInstructorAlias.alias).where(
            WcsInstructorAlias.instructor_id.in_(instructor_ids)
        )
    )
    out: dict[uuid.UUID, list[str]] = defaultdict(list)
    for instructor_id, alias in rows.all():
        out[instructor_id].append(alias)
    return dict(out)


async def list_entities(
    session: AsyncSession,
    *,
    kind: str,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[WcsEntityItem], int]:
    base = select(WcsEntity).where(
        WcsEntity.kind == kind,
        WcsEntity.merged_into_id.is_(None),
    )
    if status is not None:
        base = base.where(WcsEntity.status == status)

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        (
            await session.execute(
                base.order_by(WcsEntity.canonical_name.asc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    alias_map = await _alias_map_for_entities(session, [e.id for e in rows])
    items = [_entity_item(e, alias_map.get(e.id, [])) for e in rows]
    return items, total or 0


async def get_entity_view(
    session: AsyncSession,
    user_id: str,
    *,
    slug: str,
    kind: str,
) -> WcsEntityViewItem | None:
    row = await session.execute(
        select(WcsEntity).where(
            WcsEntity.slug == slug,
            WcsEntity.kind == kind,
            WcsEntity.merged_into_id.is_(None),
        )
    )
    entity = row.scalars().first()
    if entity is None:
        return None

    visible_ids = await visible_source_ids_for_user(session, user_id)
    alias_map = await _alias_map_for_entities(session, [entity.id])

    attr_rows = (
        (
            await session.execute(
                select(WcsSourceAttribution)
                .where(
                    WcsSourceAttribution.entity_id == entity.id,
                    WcsSourceAttribution.source_id.in_(visible_ids),
                )
                .order_by(WcsSourceAttribution.position)
            )
        )
        .scalars()
        .all()
    )

    def_rows = (
        (
            await session.execute(
                select(WcsEntityDefinition)
                .where(
                    WcsEntityDefinition.entity_id == entity.id,
                    WcsEntityDefinition.source_id.in_(visible_ids),
                )
                .order_by(WcsEntityDefinition.position)
            )
        )
        .scalars()
        .all()
    )

    rel_from = (
        (
            await session.execute(
                select(WcsEntityRelation).where(
                    WcsEntityRelation.from_entity_id == entity.id
                )
            )
        )
        .scalars()
        .all()
    )

    rel_to = (
        (
            await session.execute(
                select(WcsEntityRelation).where(
                    WcsEntityRelation.to_entity_id == entity.id
                )
            )
        )
        .scalars()
        .all()
    )

    drill_purposes: list[WcsDrillPurpose] = []
    technique_requirements: list[WcsTechniqueRequirement] = []
    if kind == "drill":
        drill_purposes = (
            (
                await session.execute(
                    select(WcsDrillPurpose).where(
                        WcsDrillPurpose.drill_entity_id == entity.id,
                        or_(
                            WcsDrillPurpose.source_id.is_(None),
                            WcsDrillPurpose.source_id.in_(visible_ids),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    elif kind == "technique":
        technique_requirements = (
            (
                await session.execute(
                    select(WcsTechniqueRequirement).where(
                        WcsTechniqueRequirement.technique_entity_id == entity.id,
                        or_(
                            WcsTechniqueRequirement.source_id.is_(None),
                            WcsTechniqueRequirement.source_id.in_(visible_ids),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )

    return WcsEntityViewItem(
        entity=_entity_item(entity, alias_map.get(entity.id, [])),
        attributions=[WcsSourceAttributionItem.model_validate(a) for a in attr_rows],
        definitions=[WcsEntityDefinitionItem.model_validate(d) for d in def_rows],
        relations_from=[WcsEntityRelationItem.model_validate(r) for r in rel_from],
        relations_to=[WcsEntityRelationItem.model_validate(r) for r in rel_to],
        drill_purposes=[WcsDrillPurposeItem.model_validate(d) for d in drill_purposes],
        technique_requirements=[
            WcsTechniqueRequirementItem.model_validate(t)
            for t in technique_requirements
        ],
    )


async def list_instructors(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
) -> tuple[list[WcsInstructorItem], int]:
    base = select(WcsInstructor).where(WcsInstructor.merged_into_id.is_(None))
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(WcsInstructor.canonical_name.asc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    alias_map = await _alias_map_for_instructors(session, [i.id for i in rows])
    return [_instructor_item(i, alias_map.get(i.id, [])) for i in rows], total or 0


async def get_instructor_view(
    session: AsyncSession,
    user_id: str,
    *,
    slug: str,
) -> WcsInstructorViewItem | None:
    row = await session.execute(
        select(WcsInstructor).where(
            WcsInstructor.slug == slug,
            WcsInstructor.merged_into_id.is_(None),
        )
    )
    instructor = row.scalars().first()
    if instructor is None:
        return None

    visible_ids = await visible_source_ids_for_user(session, user_id)
    alias_map = await _alias_map_for_instructors(session, [instructor.id])

    attr_rows = (
        (
            await session.execute(
                select(WcsSourceAttribution)
                .where(
                    WcsSourceAttribution.instructor_id == instructor.id,
                    WcsSourceAttribution.source_id.in_(visible_ids),
                )
                .order_by(WcsSourceAttribution.position)
            )
        )
        .scalars()
        .all()
    )

    def_rows = (
        (
            await session.execute(
                select(WcsEntityDefinition)
                .where(
                    WcsEntityDefinition.instructor_id == instructor.id,
                    WcsEntityDefinition.source_id.in_(visible_ids),
                )
                .order_by(WcsEntityDefinition.position)
            )
        )
        .scalars()
        .all()
    )

    ref_rows = (
        (
            await session.execute(
                select(WcsSourceReference).where(
                    WcsSourceReference.instructor_id == instructor.id,
                    WcsSourceReference.source_id.in_(visible_ids),
                )
            )
        )
        .scalars()
        .all()
    )

    return WcsInstructorViewItem(
        instructor=_instructor_item(instructor, alias_map.get(instructor.id, [])),
        attributions=[WcsSourceAttributionItem.model_validate(a) for a in attr_rows],
        definitions=[WcsEntityDefinitionItem.model_validate(d) for d in def_rows],
        referenced_in=[WcsSourceReferenceItem.model_validate(r) for r in ref_rows],
    )


async def list_sources(
    session: AsyncSession,
    user_id: str,
    *,
    limit: int,
    offset: int,
) -> tuple[list[WcsSourceItem], int]:
    visible_ids = await visible_source_ids_for_user(session, user_id)
    base = select(WcsSource).where(WcsSource.id.in_(visible_ids))
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(
                    WcsSource.session_date.desc().nullslast(),
                    WcsSource.created_at.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [_source_item(s) for s in rows], total or 0


async def get_source_view(
    session: AsyncSession,
    user_id: str,
    *,
    source_id: uuid.UUID,
) -> WcsSourceViewItem | None:
    source = await session.get(WcsSource, source_id)
    if source is None:
        return None

    visible_ids = await visible_source_ids_for_user(session, user_id)
    if source_id not in visible_ids:
        return None

    attributions = (
        (
            await session.execute(
                select(WcsSourceAttribution)
                .where(WcsSourceAttribution.source_id == source_id)
                .order_by(WcsSourceAttribution.position)
            )
        )
        .scalars()
        .all()
    )

    definitions = (
        (
            await session.execute(
                select(WcsEntityDefinition)
                .where(WcsEntityDefinition.source_id == source_id)
                .order_by(WcsEntityDefinition.position)
            )
        )
        .scalars()
        .all()
    )

    relations = (
        (
            await session.execute(
                select(WcsEntityRelation).where(
                    WcsEntityRelation.source_id == source_id
                )
            )
        )
        .scalars()
        .all()
    )

    drill_purposes = (
        (
            await session.execute(
                select(WcsDrillPurpose).where(WcsDrillPurpose.source_id == source_id)
            )
        )
        .scalars()
        .all()
    )

    technique_requirements = (
        (
            await session.execute(
                select(WcsTechniqueRequirement).where(
                    WcsTechniqueRequirement.source_id == source_id
                )
            )
        )
        .scalars()
        .all()
    )

    references = (
        (
            await session.execute(
                select(WcsSourceReference).where(
                    WcsSourceReference.source_id == source_id
                )
            )
        )
        .scalars()
        .all()
    )

    return WcsSourceViewItem(
        source=_source_item(source),
        attributions=[WcsSourceAttributionItem.model_validate(a) for a in attributions],
        definitions=[WcsEntityDefinitionItem.model_validate(d) for d in definitions],
        relations=[WcsEntityRelationItem.model_validate(r) for r in relations],
        drill_purposes=[WcsDrillPurposeItem.model_validate(d) for d in drill_purposes],
        technique_requirements=[
            WcsTechniqueRequirementItem.model_validate(t)
            for t in technique_requirements
        ],
        references=[WcsSourceReferenceItem.model_validate(r) for r in references],
    )


async def export_wiki_corpus(
    session: AsyncSession,
    user_id: str,
) -> WcsWikiExportItem:
    visible_ids = await visible_source_ids_for_user(session, user_id)
    if not visible_ids:
        return WcsWikiExportItem(
            entities=[],
            instructors=[],
            sources=[],
            attributions=[],
            definitions=[],
            relations=[],
            drill_purposes=[],
            technique_requirements=[],
            references=[],
            exported_at=dt.datetime.now(dt.UTC),
        )

    sources = (
        (await session.execute(select(WcsSource).where(WcsSource.id.in_(visible_ids))))
        .scalars()
        .all()
    )

    attributions = (
        (
            await session.execute(
                select(WcsSourceAttribution).where(
                    WcsSourceAttribution.source_id.in_(visible_ids)
                )
            )
        )
        .scalars()
        .all()
    )

    definitions = (
        (
            await session.execute(
                select(WcsEntityDefinition).where(
                    WcsEntityDefinition.source_id.in_(visible_ids)
                )
            )
        )
        .scalars()
        .all()
    )

    entity_ids_from_rows = {a.entity_id for a in attributions} | {
        d.entity_id for d in definitions
    }

    relations = (
        (
            await session.execute(
                select(WcsEntityRelation).where(
                    or_(
                        WcsEntityRelation.source_id.is_(None),
                        WcsEntityRelation.source_id.in_(visible_ids),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    for r in relations:
        entity_ids_from_rows.add(r.from_entity_id)
        entity_ids_from_rows.add(r.to_entity_id)

    drill_purposes = (
        (
            await session.execute(
                select(WcsDrillPurpose).where(
                    or_(
                        WcsDrillPurpose.source_id.is_(None),
                        WcsDrillPurpose.source_id.in_(visible_ids),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    for d in drill_purposes:
        entity_ids_from_rows.add(d.drill_entity_id)

    technique_requirements = (
        (
            await session.execute(
                select(WcsTechniqueRequirement).where(
                    or_(
                        WcsTechniqueRequirement.source_id.is_(None),
                        WcsTechniqueRequirement.source_id.in_(visible_ids),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    for t in technique_requirements:
        entity_ids_from_rows.add(t.technique_entity_id)

    references = (
        (
            await session.execute(
                select(WcsSourceReference).where(
                    WcsSourceReference.source_id.in_(visible_ids)
                )
            )
        )
        .scalars()
        .all()
    )

    instructor_ids = {a.instructor_id for a in attributions if a.instructor_id}
    instructor_ids |= {d.instructor_id for d in definitions if d.instructor_id}
    instructor_ids |= {r.instructor_id for r in references}

    entities: list[WcsEntity] = []
    if entity_ids_from_rows:
        entities = (
            (
                await session.execute(
                    select(WcsEntity).where(
                        WcsEntity.id.in_(entity_ids_from_rows),
                        WcsEntity.merged_into_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    instructors: list[WcsInstructor] = []
    if instructor_ids:
        instructors = (
            (
                await session.execute(
                    select(WcsInstructor).where(
                        WcsInstructor.id.in_(instructor_ids),
                        WcsInstructor.merged_into_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    entity_alias_map = await _alias_map_for_entities(session, [e.id for e in entities])
    instructor_alias_map = await _alias_map_for_instructors(
        session, [i.id for i in instructors]
    )

    return WcsWikiExportItem(
        entities=[_entity_item(e, entity_alias_map.get(e.id, [])) for e in entities],
        instructors=[
            _instructor_item(i, instructor_alias_map.get(i.id, [])) for i in instructors
        ],
        sources=[_source_item(s) for s in sources],
        attributions=[WcsSourceAttributionItem.model_validate(a) for a in attributions],
        definitions=[WcsEntityDefinitionItem.model_validate(d) for d in definitions],
        relations=[WcsEntityRelationItem.model_validate(r) for r in relations],
        drill_purposes=[WcsDrillPurposeItem.model_validate(d) for d in drill_purposes],
        technique_requirements=[
            WcsTechniqueRequirementItem.model_validate(t)
            for t in technique_requirements
        ],
        references=[WcsSourceReferenceItem.model_validate(r) for r in references],
        exported_at=dt.datetime.now(dt.UTC),
    )


# ── Gap-finding (admin) ─────────────────────────────────────────────────────


async def list_orphan_entities(session: AsyncSession) -> list[tuple[str, str, str]]:
    """Entities with zero attributions. Returns (slug, name, kind)."""
    attr_count = (
        select(
            WcsSourceAttribution.entity_id,
            func.count().label("cnt"),
        )
        .group_by(WcsSourceAttribution.entity_id)
        .subquery()
    )
    rows = await session.execute(
        select(WcsEntity.slug, WcsEntity.canonical_name, WcsEntity.kind)
        .outerjoin(attr_count, attr_count.c.entity_id == WcsEntity.id)
        .where(
            WcsEntity.merged_into_id.is_(None),
            or_(attr_count.c.cnt.is_(None), attr_count.c.cnt == 0),
        )
        .order_by(WcsEntity.canonical_name)
    )
    return [(r[0], r[1], r[2]) for r in rows.all()]


async def list_stub_entities(session: AsyncSession) -> list[tuple[str, str, str, int]]:
    """Entities with status stub or fewer than 2 attributions."""
    attr_count = (
        select(
            WcsSourceAttribution.entity_id,
            func.count().label("cnt"),
        )
        .group_by(WcsSourceAttribution.entity_id)
        .subquery()
    )
    cnt = func.coalesce(attr_count.c.cnt, 0)
    rows = await session.execute(
        select(WcsEntity.slug, WcsEntity.canonical_name, WcsEntity.kind, cnt)
        .outerjoin(attr_count, attr_count.c.entity_id == WcsEntity.id)
        .where(
            WcsEntity.merged_into_id.is_(None),
            or_(WcsEntity.status == "stub", cnt < 2),
        )
        .order_by(WcsEntity.canonical_name)
    )
    return [(r[0], r[1], r[2], int(r[3])) for r in rows.all()]


async def list_unpaired_skill_slugs(session: AsyncSession) -> list[tuple[str, str]]:
    """Skill slugs in drill_purposes but not technique_requirements, or vice versa."""
    drill_slugs = select(WcsDrillPurpose.skill_slug).distinct()
    tech_slugs = select(WcsTechniqueRequirement.skill_slug).distinct()

    only_drill = await session.execute(
        select(WcsDrillPurpose.skill_slug, WcsDrillPurpose.skill_name)
        .where(WcsDrillPurpose.skill_slug.not_in(tech_slugs))
        .distinct()
    )
    only_tech = await session.execute(
        select(
            WcsTechniqueRequirement.skill_slug,
            WcsTechniqueRequirement.skill_name,
        )
        .where(WcsTechniqueRequirement.skill_slug.not_in(drill_slugs))
        .distinct()
    )
    out: list[tuple[str, str]] = []
    for slug, name in only_drill.all():
        out.append((slug, f"drill only: {name}"))
    for slug, name in only_tech.all():
        out.append((slug, f"technique only: {name}"))
    return out


async def list_uncomposed_sources(session: AsyncSession) -> list[tuple[str, str]]:
    """Sources with active extraction but zero attributions."""
    attr_count = (
        select(
            WcsSourceAttribution.source_id,
            func.count().label("cnt"),
        )
        .group_by(WcsSourceAttribution.source_id)
        .subquery()
    )
    rows = await session.execute(
        select(WcsSource.id, WcsSource.title)
        .join(
            WcsSourceExtraction,
            (WcsSourceExtraction.source_id == WcsSource.id)
            & WcsSourceExtraction.is_active.is_(True),
        )
        .outerjoin(attr_count, attr_count.c.source_id == WcsSource.id)
        .where(or_(attr_count.c.cnt.is_(None), attr_count.c.cnt == 0))
        .order_by(WcsSource.created_at.desc())
    )
    return [(str(r[0]), r[1] or "") for r in rows.all()]
