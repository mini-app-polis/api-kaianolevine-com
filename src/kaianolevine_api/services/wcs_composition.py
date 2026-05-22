"""Composition Service — deterministic Layer 1 → Layer 2 derivation for WCS sources."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from mini_app_polis.logger import LOG_START, LOG_SUCCESS, LOG_WARNING, get_logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    WcsAttributionAddition,
    WcsAttributionCorrection,
    WcsDrillPurpose,
    WcsDrillPurposeAddition,
    WcsEntity,
    WcsEntityAlias,
    WcsEntityDefinition,
    WcsEntityRelation,
    WcsEntityRelationAddition,
    WcsInstructor,
    WcsInstructorAlias,
    WcsNameCorrection,
    WcsSource,
    WcsSourceAttribution,
    WcsSourceExtraction,
    WcsSourceReference,
    WcsTechniqueRequirement,
    WcsTechniqueRequirementAddition,
)
from ..schemas import WcsExtractionRawOutput

log = get_logger()

_COMPETITION_STRATEGY_NAME = "Competition Strategy"


@dataclass(frozen=True)
class CompositionResult:
    """Counts of canonical rows written by compose_source."""

    attributions_written: int = 0
    definitions_written: int = 0
    relations_written: int = 0
    drill_purposes_written: int = 0
    technique_requirements_written: int = 0
    references_written: int = 0

    @classmethod
    def from_counts(cls, counts: dict[str, int]) -> CompositionResult:
        """Build a CompositionResult from a counts dict keyed by canonical-row category."""
        return cls(
            attributions_written=counts.get("attributions", 0),
            definitions_written=counts.get("definitions", 0),
            relations_written=counts.get("relations", 0),
            drill_purposes_written=counts.get("drill_purposes", 0),
            technique_requirements_written=counts.get("technique_requirements", 0),
            references_written=counts.get("references", 0),
        )


def slugify(name: str) -> str:
    """Lowercase, hyphenate, ASCII-only, strip apostrophes."""
    s = name.strip().lower()
    s = s.replace("'", "").replace("\u2019", "")
    s = s.replace("&", " ")
    s = re.sub(r"[^a-z0-9\-]+", " ", s)
    s = re.sub(r"[\s\-]+", "-", s.strip())
    return s.strip("-")


def depluralize(slug: str) -> str:
    """Naive English depluralization for alias matching."""
    if slug.endswith("ies") and len(slug) > 4:
        return slug[:-3] + "y"
    if slug.endswith("es") and len(slug) > 3:
        return slug[:-2]
    # Hyphenated slugs only for trailing single-s (e.g. anchor-steps → anchor-step).
    if slug.endswith("s") and not slug.endswith("ss") and "-" in slug and len(slug) > 2:
        return slug[:-1]
    return slug


async def apply_name_corrections(
    session: AsyncSession,
    raw_name: str,
    source_id: uuid.UUID | None = None,
) -> str:
    """Apply name corrections: source-scoped first, then global."""
    name = raw_name
    if source_id is not None:
        result = await session.execute(
            select(WcsNameCorrection).where(
                WcsNameCorrection.raw_name == raw_name,
                WcsNameCorrection.source_id == source_id,
            )
        )
        row = result.scalars().first()
        if row is not None:
            return row.corrected_name

    result = await session.execute(
        select(WcsNameCorrection).where(
            WcsNameCorrection.raw_name == raw_name,
            WcsNameCorrection.scope == "global",
        )
    )
    row = result.scalars().first()
    if row is not None:
        return row.corrected_name
    return name


async def _find_entity_by_slug(session: AsyncSession, slug: str) -> WcsEntity | None:
    result = await session.execute(
        select(WcsEntity).where(
            WcsEntity.slug == slug,
            WcsEntity.merged_into_id.is_(None),
        )
    )
    return result.scalars().first()


async def _find_entity_by_alias(session: AsyncSession, alias: str) -> WcsEntity | None:
    result = await session.execute(
        select(WcsEntity)
        .join(WcsEntityAlias, WcsEntityAlias.entity_id == WcsEntity.id)
        .where(
            WcsEntityAlias.alias == alias,
            WcsEntity.merged_into_id.is_(None),
        )
    )
    return result.scalars().first()


async def resolve_entity(
    session: AsyncSession,
    name: str,
    kind: str,
) -> WcsEntity:
    """Find-or-create an entity by name + kind."""
    slug = slugify(name)
    entity = await _find_entity_by_slug(session, slug)
    if entity is not None:
        return entity

    entity = await _find_entity_by_alias(session, slug)
    if entity is not None:
        return entity

    depluralized = depluralize(slug)
    if depluralized != slug:
        entity = await _find_entity_by_slug(session, depluralized)
        if entity is not None:
            return entity
        entity = await _find_entity_by_alias(session, depluralized)
        if entity is not None:
            return entity

    entity = WcsEntity(
        slug=slug,
        canonical_name=name.strip(),
        kind=kind,
    )
    session.add(entity)
    await session.flush()
    session.add(
        WcsEntityAlias(
            entity_id=entity.id,
            alias=slug,
            origin="extraction",
        )
    )
    await session.flush()
    return entity


async def _find_instructor_by_slug(
    session: AsyncSession, slug: str
) -> WcsInstructor | None:
    result = await session.execute(
        select(WcsInstructor).where(
            WcsInstructor.slug == slug,
            WcsInstructor.merged_into_id.is_(None),
        )
    )
    return result.scalars().first()


async def _find_instructor_by_alias(
    session: AsyncSession, alias: str
) -> WcsInstructor | None:
    result = await session.execute(
        select(WcsInstructor)
        .join(WcsInstructorAlias, WcsInstructorAlias.instructor_id == WcsInstructor.id)
        .where(
            WcsInstructorAlias.alias == alias,
            WcsInstructor.merged_into_id.is_(None),
        )
    )
    return result.scalars().first()


async def resolve_instructor(
    session: AsyncSession,
    name: str,
    source_id: uuid.UUID | None = None,
) -> WcsInstructor:
    """Find-or-create an instructor by name (with name corrections applied)."""
    corrected = await apply_name_corrections(session, name, source_id)
    slug = slugify(corrected)

    instructor = await _find_instructor_by_slug(session, slug)
    if instructor is not None:
        return instructor

    instructor = await _find_instructor_by_alias(session, slug)
    if instructor is not None:
        return instructor

    instructor = WcsInstructor(
        slug=slug,
        canonical_name=corrected.strip(),
    )
    session.add(instructor)
    await session.flush()
    session.add(
        WcsInstructorAlias(
            instructor_id=instructor.id,
            alias=slug,
            origin="extraction",
        )
    )
    await session.flush()
    return instructor


async def _resolve_entity_by_slug(session: AsyncSession, entity_slug: str) -> WcsEntity:
    entity = await _find_entity_by_slug(session, entity_slug)
    if entity is not None:
        return entity
    entity = await _find_entity_by_alias(session, entity_slug)
    if entity is not None:
        return entity
    raise ValueError(f"Unknown entity slug: {entity_slug}")


def _correction_matches_target(
    target: dict,
    *,
    raw_term: str,
    position: int | None = None,
) -> bool:
    if "raw_term" in target and target["raw_term"].lower() != raw_term.lower():
        return False
    if "entity_name" in target and target["entity_name"].lower() != raw_term.lower():
        return False
    if "position" in target and position is not None and target["position"] != position:
        return False
    return True


def _apply_entity_extraction_corrections(
    *,
    name: str,
    kind: str,
    prose: str,
    raw_term: str,
    position: int,
    corrections: list[WcsAttributionCorrection],
) -> tuple[str, str, str]:
    """Apply attribution_corrections targeting an extracted entity claim."""
    for corr in corrections:
        if not _correction_matches_target(
            corr.attribution_target, raw_term=raw_term, position=position
        ):
            continue
        val = corr.corrected_value
        if corr.field == "entity":
            if isinstance(val, dict) and "name" in val:
                name = str(val["name"])
            elif isinstance(val, str):
                name = val
        elif corr.field == "kind":
            if isinstance(val, str):
                kind = val
            elif isinstance(val, dict) and "kind" in val:
                kind = str(val["kind"])
        elif corr.field == "prose":
            if isinstance(val, str):
                prose = val
            elif isinstance(val, dict) and "prose" in val:
                prose = str(val["prose"])
    return name, kind, prose


async def compose_source(
    session: AsyncSession,
    source_id: uuid.UUID,
) -> CompositionResult:
    """Re-derive canonical layer rows for a single source.

    Reads the active source_extraction plus all applicable corrections and
    additions. Writes wcs_source_attributions, wcs_entity_definitions,
    wcs_entity_relations, wcs_drill_purposes, wcs_technique_requirements,
    wcs_source_references rows. Creates wcs_entities and wcs_instructors
    rows as needed via entity resolution.

    Idempotent: re-running for the same source with the same inputs produces
    the same canonical state. Existing canonical rows for the source are
    deleted before re-composition (DELETE THEN INSERT pattern), so polish
    prose on entity rows is preserved (it lives on wcs_entities.overview_md,
    not on source-attributed rows).

    # Invocation model

    Designed to be called synchronously inside a request lifecycle (notably
    by POST /v1/wcs/sources and the admin recompose endpoints). The caller
    holds the AsyncSession and the transaction; this function does not
    commit or roll back — it writes rows and lets the caller commit. This
    keeps the composition + source-write atomic from the caller's
    perspective: either both succeed and commit together, or neither does.

    Runtime is O(extraction size) — typically well under a second for a
    single lesson at proof-of-concept scale. See routers/wcs_sources.py
    module docstring for when to move callers to async invocation.
    """
    log.info("%s compose_source source_id=%s", LOG_START, source_id)

    source = await session.get(WcsSource, source_id)
    if source is None:
        log.warning("%s source not found id=%s", LOG_WARNING, source_id)
        return CompositionResult()

    ext_result = await session.execute(
        select(WcsSourceExtraction).where(
            WcsSourceExtraction.source_id == source_id,
            WcsSourceExtraction.is_active.is_(True),
        )
    )
    extraction = ext_result.scalars().first()
    if extraction is None:
        log.warning("%s no active extraction for source_id=%s", LOG_WARNING, source_id)
        return CompositionResult()

    corr_result = await session.execute(
        select(WcsAttributionCorrection).where(
            WcsAttributionCorrection.source_id == source_id
        )
    )
    attribution_corrections = list(corr_result.scalars().all())

    attr_add_result = await session.execute(
        select(WcsAttributionAddition).where(
            (WcsAttributionAddition.source_id == source_id)
            | (WcsAttributionAddition.source_id.is_(None))
        )
    )
    attribution_additions = list(attr_add_result.scalars().all())

    drill_add_result = await session.execute(
        select(WcsDrillPurposeAddition).where(
            (WcsDrillPurposeAddition.source_id == source_id)
            | (WcsDrillPurposeAddition.source_id.is_(None))
        )
    )
    drill_additions = list(drill_add_result.scalars().all())

    tech_add_result = await session.execute(
        select(WcsTechniqueRequirementAddition).where(
            (WcsTechniqueRequirementAddition.source_id == source_id)
            | (WcsTechniqueRequirementAddition.source_id.is_(None))
        )
    )
    technique_additions = list(tech_add_result.scalars().all())

    rel_add_result = await session.execute(select(WcsEntityRelationAddition))
    relation_additions = list(rel_add_result.scalars().all())

    await session.execute(
        delete(WcsSourceAttribution).where(WcsSourceAttribution.source_id == source_id)
    )
    await session.execute(
        delete(WcsEntityDefinition).where(WcsEntityDefinition.source_id == source_id)
    )
    await session.execute(
        delete(WcsEntityRelation).where(WcsEntityRelation.source_id == source_id)
    )
    await session.execute(
        delete(WcsDrillPurpose).where(WcsDrillPurpose.source_id == source_id)
    )
    await session.execute(
        delete(WcsTechniqueRequirement).where(
            WcsTechniqueRequirement.source_id == source_id
        )
    )
    await session.execute(
        delete(WcsSourceReference).where(WcsSourceReference.source_id == source_id)
    )

    counts = {
        "attributions": 0,
        "definitions": 0,
        "relations": 0,
        "drill_purposes": 0,
        "technique_requirements": 0,
        "references": 0,
    }

    default_instructor_ids: list[uuid.UUID] = []
    for raw_instructor in source.instructors_raw:
        instructor = await resolve_instructor(session, raw_instructor, source_id)
        default_instructor_ids.append(instructor.id)

    raw_dict = dict(extraction.raw_output)
    for rel in raw_dict.get("entity_relations") or []:
        if "from" not in rel and "from_" in rel:
            rel["from"] = rel.pop("from_")
    raw_output = WcsExtractionRawOutput.model_validate(raw_dict)

    for position, ent in enumerate(raw_output.entities):
        name, kind, prose = _apply_entity_extraction_corrections(
            name=ent.name,
            kind=ent.kind,
            prose=ent.prose,
            raw_term=ent.name,
            position=position,
            corrections=attribution_corrections,
        )
        entity = await resolve_entity(session, name, kind)
        if ent.external_origin:
            entity.external_origin = ent.external_origin
            await session.flush()

        instructor_targets: list[uuid.UUID | None] = (
            list(default_instructor_ids) if default_instructor_ids else [None]
        )
        for instructor_id in instructor_targets:
            session.add(
                WcsSourceAttribution(
                    source_id=source_id,
                    entity_id=entity.id,
                    instructor_id=instructor_id,
                    attribution_kind="taught",
                    prose=prose,
                    raw_term=ent.name,
                    position=position,
                    origin="extraction",
                )
            )
            counts["attributions"] += 1

    for pos, defn in enumerate(raw_output.entity_definitions):
        entity = await resolve_entity(session, defn.entity_name, "concept")
        def_instructor_targets: list[uuid.UUID | None] = (
            list(default_instructor_ids) if default_instructor_ids else [None]
        )
        for instructor_id in def_instructor_targets:
            session.add(
                WcsEntityDefinition(
                    entity_id=entity.id,
                    source_id=source_id,
                    instructor_id=instructor_id,
                    term=defn.entity_name,
                    definition=defn.definition,
                    position=pos,
                    origin="extraction",
                )
            )
            counts["definitions"] += 1

    for rel in raw_output.entity_relations:
        from_entity = await resolve_entity(session, rel.from_, "concept")
        to_entity = await resolve_entity(session, rel.to, "concept")
        session.add(
            WcsEntityRelation(
                from_entity_id=from_entity.id,
                to_entity_id=to_entity.id,
                relation_kind=rel.relation_kind,
                source_id=source_id,
                prose=rel.prose,
                origin="extraction",
            )
        )
        counts["relations"] += 1

    for dp in raw_output.drill_purposes:
        drill = await resolve_entity(session, dp.drill_name, "drill")
        session.add(
            WcsDrillPurpose(
                drill_entity_id=drill.id,
                source_id=source_id,
                skill_name=dp.skill_description,
                skill_slug=slugify(dp.skill_description),
                prose="",
                focus_context=dp.focus_context,
                origin="extraction",
            )
        )
        counts["drill_purposes"] += 1

    for tr in raw_output.technique_requirements:
        technique = await resolve_entity(session, tr.technique_name, "technique")
        session.add(
            WcsTechniqueRequirement(
                technique_entity_id=technique.id,
                source_id=source_id,
                skill_name=tr.skill_description,
                skill_slug=slugify(tr.skill_description),
                prose="",
                origin="extraction",
            )
        )
        counts["technique_requirements"] += 1

    for pos, mistake in enumerate(raw_output.common_mistakes):
        if mistake.entity_name is None:
            continue
        entity = await resolve_entity(session, mistake.entity_name, "concept")
        mistake_instructor_targets: list[uuid.UUID | None] = (
            list(default_instructor_ids) if default_instructor_ids else [None]
        )
        for instructor_id in mistake_instructor_targets:
            session.add(
                WcsSourceAttribution(
                    source_id=source_id,
                    entity_id=entity.id,
                    instructor_id=instructor_id,
                    attribution_kind="mistake",
                    prose="",
                    raw_term=mistake.entity_name,
                    position=pos,
                    mistake_text=mistake.mistake,
                    correction_text=mistake.correction,
                    origin="extraction",
                )
            )
            counts["attributions"] += 1

    competition_entity = await resolve_entity(
        session, _COMPETITION_STRATEGY_NAME, "concept"
    )
    for pos, comp in enumerate(raw_output.competition_notes):
        if comp.entity_name:
            entity = await resolve_entity(session, comp.entity_name, "concept")
        else:
            entity = competition_entity
        note_prose = comp.note
        if comp.context:
            note_prose = f"{comp.note} ({comp.context})"
        comp_instructor_targets: list[uuid.UUID | None] = (
            list(default_instructor_ids) if default_instructor_ids else [None]
        )
        for instructor_id in comp_instructor_targets:
            session.add(
                WcsSourceAttribution(
                    source_id=source_id,
                    entity_id=entity.id,
                    instructor_id=instructor_id,
                    attribution_kind="competition_note",
                    prose=note_prose,
                    raw_term=comp.entity_name or "",
                    position=pos,
                    origin="extraction",
                )
            )
            counts["attributions"] += 1

    for ref in raw_output.references:
        instructor = await resolve_instructor(session, ref.name, source_id)
        session.add(
            WcsSourceReference(
                source_id=source_id,
                instructor_id=instructor.id,
                context=ref.context,
                ref_type=ref.type or "",
                origin="extraction",
            )
        )
        counts["references"] += 1

    for addition in attribution_additions:
        if addition.source_id is not None and addition.source_id != source_id:
            continue
        entity = await _resolve_entity_by_slug(session, addition.entity_slug)
        instructor_id: uuid.UUID | None = None
        if addition.instructor_slug:
            instructor = await _find_instructor_by_slug(
                session, addition.instructor_slug
            )
            if instructor is None:
                instructor = await _find_instructor_by_alias(
                    session, addition.instructor_slug
                )
            instructor_id = instructor.id if instructor else None
        session.add(
            WcsSourceAttribution(
                source_id=source_id,
                entity_id=entity.id,
                instructor_id=instructor_id,
                attribution_kind=addition.attribution_kind,
                prose=addition.prose,
                raw_term="",
                origin="manual",
            )
        )
        counts["attributions"] += 1

    for addition in drill_additions:
        if addition.source_id is not None and addition.source_id != source_id:
            continue
        drill = await _resolve_entity_by_slug(session, addition.drill_entity_slug)
        session.add(
            WcsDrillPurpose(
                drill_entity_id=drill.id,
                source_id=source_id,
                skill_name=addition.skill_name,
                skill_slug=slugify(addition.skill_name),
                prose=addition.prose,
                focus_context=addition.focus_context,
                origin="manual",
            )
        )
        counts["drill_purposes"] += 1

    for addition in technique_additions:
        if addition.source_id is not None and addition.source_id != source_id:
            continue
        technique = await _resolve_entity_by_slug(
            session, addition.technique_entity_slug
        )
        session.add(
            WcsTechniqueRequirement(
                technique_entity_id=technique.id,
                source_id=source_id,
                skill_name=addition.skill_name,
                skill_slug=slugify(addition.skill_name),
                prose=addition.prose,
                origin="manual",
            )
        )
        counts["technique_requirements"] += 1

    for addition in relation_additions:
        from_entity = await _resolve_entity_by_slug(session, addition.from_entity_slug)
        to_entity = await _resolve_entity_by_slug(session, addition.to_entity_slug)
        session.add(
            WcsEntityRelation(
                from_entity_id=from_entity.id,
                to_entity_id=to_entity.id,
                relation_kind=addition.relation_kind,
                source_id=source_id,
                prose=addition.prose,
                origin="manual",
            )
        )
        counts["relations"] += 1

    result = CompositionResult.from_counts(counts)

    log.info(
        "%s compose_source source_id=%s attributions=%d definitions=%d "
        "relations=%d drill_purposes=%d technique_requirements=%d references=%d",
        LOG_SUCCESS,
        source_id,
        result.attributions_written,
        result.definitions_written,
        result.relations_written,
        result.drill_purposes_written,
        result.technique_requirements_written,
        result.references_written,
    )
    return result
