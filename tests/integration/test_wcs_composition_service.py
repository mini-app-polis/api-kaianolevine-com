"""Tests for the WCS Composition Service."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.models import (
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
    WcsNameCorrection,
    WcsSource,
    WcsSourceAttribution,
    WcsSourceExtraction,
    WcsSourceReference,
    WcsTechniqueRequirement,
    WcsTechniqueRequirementAddition,
    WcsTranscript,
)
from kaianolevine_api.services.wcs_composition import (
    apply_name_corrections,
    compose_source,
    depluralize,
    resolve_entity,
    resolve_instructor,
    slugify,
)
from kaianolevine_api.services.wcs_wiki import get_instructor_view


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        yield session


# ── slugify / depluralize ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Anchor Step", "anchor-step"),
        ("Don't push", "dont-push"),
        ("sugar push & whip", "sugar-push-whip"),
        ("rock-and-go", "rock-and-go"),
        ("", ""),
        ("  Multiple   Spaces  ", "multiple-spaces"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert slugify(name) == expected


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("sugar-pushes", "sugar-push"),
        ("anchor-steps", "anchor-step"),
        ("whip-bashes", "whip-bash"),
        ("anchor-step", "anchor-step"),
        ("glass", "glass"),
    ],
)
def test_depluralize(slug: str, expected: str) -> None:
    assert depluralize(slug) == expected


# ── resolve_entity / resolve_instructor ───────────────────────────────────────


async def test_resolve_entity_direct_slug(db_session: AsyncSession) -> None:
    existing = WcsEntity(
        slug="anchor-step", canonical_name="Anchor Step", kind="technique"
    )
    db_session.add(existing)
    await db_session.commit()

    found = await resolve_entity(db_session, "Anchor Step", "concept")
    assert found.id == existing.id
    assert found.kind == "technique"


async def test_resolve_entity_alias_match(db_session: AsyncSession) -> None:
    entity = WcsEntity(slug="sugar-push", canonical_name="Sugar Push", kind="pattern")
    db_session.add(entity)
    await db_session.flush()
    db_session.add(
        WcsEntityAlias(entity_id=entity.id, alias="sugar-pushes", origin="extraction")
    )
    await db_session.commit()

    found = await resolve_entity(db_session, "sugar pushes", "pattern")
    assert found.id == entity.id


async def test_resolve_entity_depluralized_match(db_session: AsyncSession) -> None:
    entity = WcsEntity(
        slug="anchor-step", canonical_name="Anchor Step", kind="technique"
    )
    db_session.add(entity)
    await db_session.commit()

    found = await resolve_entity(db_session, "anchor steps", "technique")
    assert found.id == entity.id


async def test_resolve_entity_creates_new(db_session: AsyncSession) -> None:
    created = await resolve_entity(db_session, "New Concept", "concept")
    await db_session.commit()
    assert created.slug == "new-concept"
    assert created.kind == "concept"
    aliases = (
        (
            await db_session.execute(
                select(WcsEntityAlias).where(WcsEntityAlias.entity_id == created.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(aliases) == 1


async def test_resolve_instructor_with_name_correction(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        WcsNameCorrection(
            raw_name="Roberta",
            corrected_name="Robert",
            scope="global",
        )
    )
    await db_session.commit()

    instructor = await resolve_instructor(db_session, "Roberta")
    assert instructor.canonical_name == "Robert"
    assert instructor.slug == "robert"


async def test_apply_name_corrections_source_before_global(
    db_session: AsyncSession,
) -> None:
    source_id = uuid.uuid4()
    db_session.add(
        WcsNameCorrection(
            raw_name="Kate",
            corrected_name="Kate Global",
            scope="global",
        )
    )
    db_session.add(
        WcsNameCorrection(
            raw_name="Kate",
            corrected_name="Kate Local",
            scope="source",
            source_id=source_id,
        )
    )
    await db_session.commit()

    assert await apply_name_corrections(db_session, "Kate", source_id) == "Kate Local"
    assert await apply_name_corrections(db_session, "Kate") == "Kate Global"


# ── compose_source ───────────────────────────────────────────────────────────


def _sample_raw_output() -> dict:
    return {
        "title": "Frame lesson",
        "entities": [
            {
                "kind": "technique",
                "name": "Anchor Step",
                "prose": "Stay grounded on count 3.",
            },
            {"kind": "drill", "name": "Paper Drill", "prose": "Walk slowly."},
        ],
        "entity_definitions": [
            {"entity_name": "frame", "definition": "Upper-body connection."},
        ],
        "entity_relations": [
            {
                "from": "Paper Drill",
                "to": "Anchor Step",
                "relation_kind": "drill_trains_technique",
            }
        ],
        "drill_purposes": [
            {
                "drill_name": "Paper Drill",
                "skill_description": "Weight Transfer",
                "focus_context": "slow tempo",
            }
        ],
        "technique_requirements": [
            {
                "technique_name": "Anchor Step",
                "skill_description": "Weight Transfer",
            }
        ],
        "common_mistakes": [
            {
                "entity_name": "Anchor Step",
                "mistake": "Rushing the anchor",
                "correction": "Hold through count 3",
            }
        ],
        "competition_notes": [{"note": "Judges watch posture", "entity_name": None}],
        "references": [{"name": "Ben Morris", "type": "judge", "context": "mentioned"}],
    }


async def _seed_source_with_extraction(
    db_session: AsyncSession,
    *,
    raw_output: dict | None = None,
    instructors_raw: list[str] | None = None,
    drive_file_id: str | None = None,
) -> tuple[WcsSource, WcsSourceExtraction]:
    transcript = WcsTranscript(
        owner_id="dev-owner",
        raw_text="Transcript text.",
        source_type="plaud",
        source_filename="lesson.txt",
        drive_file_id=drive_file_id or f"drive-{uuid.uuid4().hex[:12]}",
    )
    db_session.add(transcript)
    await db_session.flush()

    source = WcsSource(
        owner_id="dev-owner",
        transcript_id=transcript.id,
        instructors_raw=instructors_raw if instructors_raw is not None else ["Kaiano"],
        students_raw=["Sarah"],
    )
    db_session.add(source)
    await db_session.flush()

    extraction = WcsSourceExtraction(
        source_id=source.id,
        extractor_version="1.0.0",
        extractor_model="claude",
        extractor_provider="anthropic",
        prompt_version="v1",
        raw_output=raw_output or _sample_raw_output(),
        is_active=True,
    )
    db_session.add(extraction)
    await db_session.commit()
    return source, extraction


async def test_compose_source_writes_canonical_rows(db_session: AsyncSession) -> None:
    source, _ = await _seed_source_with_extraction(db_session)

    await compose_source(db_session, source.id)
    await db_session.commit()

    attr_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WcsSourceAttribution)
            .where(WcsSourceAttribution.source_id == source.id)
        )
    ).scalar_one()
    assert attr_count >= 3

    extraction_attrs = (
        (
            await db_session.execute(
                select(WcsSourceAttribution).where(
                    WcsSourceAttribution.source_id == source.id,
                    WcsSourceAttribution.origin == "extraction",
                )
            )
        )
        .scalars()
        .all()
    )
    assert extraction_attrs
    assert all(a.instructor_id is None for a in extraction_attrs)

    defs = (
        (
            await db_session.execute(
                select(WcsEntityDefinition).where(
                    WcsEntityDefinition.source_id == source.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(defs) >= 1

    relations = (
        (
            await db_session.execute(
                select(WcsEntityRelation).where(
                    WcsEntityRelation.source_id == source.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(relations) >= 1

    drills = (
        (
            await db_session.execute(
                select(WcsDrillPurpose).where(WcsDrillPurpose.source_id == source.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(drills) == 1
    assert drills[0].skill_slug == "weight-transfer"

    techs = (
        (
            await db_session.execute(
                select(WcsTechniqueRequirement).where(
                    WcsTechniqueRequirement.source_id == source.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(techs) == 1

    refs = (
        (
            await db_session.execute(
                select(WcsSourceReference).where(
                    WcsSourceReference.source_id == source.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(refs) == 1
    assert refs[0].referenced_name == "Ben Morris"
    assert refs[0].ref_type == "judge"

    instructors = (await db_session.execute(select(WcsInstructor))).scalars().all()
    assert len(instructors) == 1
    assert instructors[0].canonical_name == "Kaiano"


async def test_compose_source_does_not_create_instructor_from_references(
    db_session: AsyncSession,
) -> None:
    """References are stored as raw name strings only — no wcs_instructors rows."""
    raw = _sample_raw_output()
    raw["entities"] = []
    raw["entity_definitions"] = []
    raw["entity_relations"] = []
    raw["drill_purposes"] = []
    raw["technique_requirements"] = []
    raw["common_mistakes"] = []
    raw["competition_notes"] = []
    raw["references"] = [
        {"name": "Robert", "type": "teacher", "context": "quoted"},
        {"name": "Amy", "type": "dancer", "context": "mentioned"},
        {"name": "Skippy", "type": "judge", "context": "cited"},
    ]
    source, _ = await _seed_source_with_extraction(
        db_session, raw_output=raw, instructors_raw=["Kaiano"]
    )

    await compose_source(db_session, source.id)
    await db_session.commit()

    instructors = (await db_session.execute(select(WcsInstructor))).scalars().all()
    assert len(instructors) == 1
    assert instructors[0].canonical_name == "Kaiano"

    refs = (
        (
            await db_session.execute(
                select(WcsSourceReference).where(
                    WcsSourceReference.source_id == source.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(refs) == 3
    assert {r.referenced_name for r in refs} == {"Robert", "Amy", "Skippy"}


async def test_compose_source_idempotent(db_session: AsyncSession) -> None:
    source, _ = await _seed_source_with_extraction(db_session)
    await compose_source(db_session, source.id)
    await db_session.commit()
    first_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WcsSourceAttribution)
            .where(WcsSourceAttribution.source_id == source.id)
        )
    ).scalar_one()

    await compose_source(db_session, source.id)
    await db_session.commit()
    second_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WcsSourceAttribution)
            .where(WcsSourceAttribution.source_id == source.id)
        )
    ).scalar_one()
    assert first_count == second_count


async def test_compose_source_attribution_correction_overrides(
    db_session: AsyncSession,
) -> None:
    source, _ = await _seed_source_with_extraction(db_session)
    db_session.add(
        WcsAttributionCorrection(
            source_id=source.id,
            attribution_target={"raw_term": "Anchor Step", "position": 0},
            field="prose",
            corrected_value={"prose": "Corrected framing."},
        )
    )
    await db_session.commit()

    await compose_source(db_session, source.id)
    await db_session.commit()

    taught = (
        (
            await db_session.execute(
                select(WcsSourceAttribution).where(
                    WcsSourceAttribution.source_id == source.id,
                    WcsSourceAttribution.attribution_kind == "taught",
                    WcsSourceAttribution.raw_term == "Anchor Step",
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(a.prose == "Corrected framing." for a in taught)


async def test_compose_source_additions_append(db_session: AsyncSession) -> None:
    source, _ = await _seed_source_with_extraction(db_session)
    await compose_source(db_session, source.id)
    await db_session.commit()

    entity = await resolve_entity(db_session, "Settle", "concept")
    await db_session.commit()

    db_session.add(
        WcsAttributionAddition(
            source_id=source.id,
            entity_slug=entity.slug,
            attribution_kind="taught",
            prose="Manual settle note.",
        )
    )
    db_session.add(
        WcsDrillPurposeAddition(
            source_id=source.id,
            drill_entity_slug="paper-drill",
            skill_name="Balance",
            prose="Added purpose.",
        )
    )
    db_session.add(
        WcsTechniqueRequirementAddition(
            source_id=source.id,
            technique_entity_slug="anchor-step",
            skill_name="Posture",
        )
    )
    db_session.add(
        WcsEntityRelationAddition(
            from_entity_slug="paper-drill",
            to_entity_slug="anchor-step",
            relation_kind="manual_link",
            prose="Operator added.",
        )
    )
    await db_session.commit()

    await compose_source(db_session, source.id)
    await db_session.commit()

    manual_attrs = (
        (
            await db_session.execute(
                select(WcsSourceAttribution).where(
                    WcsSourceAttribution.source_id == source.id,
                    WcsSourceAttribution.origin == "manual",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(manual_attrs) >= 1

    manual_drills = (
        (
            await db_session.execute(
                select(WcsDrillPurpose).where(
                    WcsDrillPurpose.source_id == source.id,
                    WcsDrillPurpose.origin == "manual",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(manual_drills) >= 1


async def test_compose_preserves_entity_overview_md(db_session: AsyncSession) -> None:
    source, _ = await _seed_source_with_extraction(db_session)
    entity = await resolve_entity(db_session, "Anchor Step", "technique")
    entity.overview_md = "Polished overview prose."
    await db_session.commit()

    await compose_source(db_session, source.id)
    await db_session.commit()

    refreshed = await db_session.get(WcsEntity, entity.id)
    assert refreshed is not None
    assert refreshed.overview_md == "Polished overview prose."


async def test_composer_writes_one_row_per_extracted_item_regardless_of_instructor_count(
    db_session: AsyncSession,
) -> None:
    """Co-taught sources get the same row count as single-instructor; all NULL instructor_id."""
    single_source, _ = await _seed_source_with_extraction(
        db_session, instructors_raw=["Kaiano"]
    )
    await compose_source(db_session, single_source.id)
    await db_session.commit()

    single_attr_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WcsSourceAttribution)
            .where(
                WcsSourceAttribution.source_id == single_source.id,
                WcsSourceAttribution.origin == "extraction",
            )
        )
    ).scalar_one()
    single_def_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WcsEntityDefinition)
            .where(
                WcsEntityDefinition.source_id == single_source.id,
                WcsEntityDefinition.origin == "extraction",
            )
        )
    ).scalar_one()

    co_source, _ = await _seed_source_with_extraction(
        db_session,
        instructors_raw=["Kaiano", "Amy"],
    )
    await compose_source(db_session, co_source.id)
    await db_session.commit()

    co_attr_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WcsSourceAttribution)
            .where(
                WcsSourceAttribution.source_id == co_source.id,
                WcsSourceAttribution.origin == "extraction",
            )
        )
    ).scalar_one()
    co_def_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WcsEntityDefinition)
            .where(
                WcsEntityDefinition.source_id == co_source.id,
                WcsEntityDefinition.origin == "extraction",
            )
        )
    ).scalar_one()

    assert co_attr_count == single_attr_count
    assert co_def_count == single_def_count

    co_attrs = (
        (
            await db_session.execute(
                select(WcsSourceAttribution).where(
                    WcsSourceAttribution.source_id == co_source.id,
                    WcsSourceAttribution.origin == "extraction",
                )
            )
        )
        .scalars()
        .all()
    )
    assert all(a.instructor_id is None for a in co_attrs)


async def test_get_instructor_view_returns_coauth_rows_with_null_instructor_id(
    db_session: AsyncSession,
) -> None:
    kaiano = await resolve_instructor(db_session, "Kaiano")
    await resolve_instructor(db_session, "Amy")
    await resolve_instructor(db_session, "Bob")
    entity = await resolve_entity(db_session, "Frame", "concept")
    await db_session.commit()

    transcript = WcsTranscript(
        owner_id="dev-owner",
        raw_text="Co-taught lesson.",
        source_type="plaud",
        source_filename="co-lesson.txt",
        drive_file_id="drive-co",
    )
    db_session.add(transcript)
    await db_session.flush()

    source = WcsSource(
        owner_id="dev-owner",
        transcript_id=transcript.id,
        instructors_raw=["Kaiano", "Amy"],
        students_raw=["Sarah"],
        is_default_visible=True,
    )
    db_session.add(source)
    await db_session.flush()

    db_session.add(
        WcsSourceAttribution(
            source_id=source.id,
            entity_id=entity.id,
            instructor_id=None,
            attribution_kind="taught",
            prose="Connection framing.",
            raw_term="Frame",
            position=0,
            origin="extraction",
        )
    )
    await db_session.commit()

    kaiano_view = await get_instructor_view(db_session, "viewer", slug=kaiano.slug)
    assert kaiano_view is not None
    assert len(kaiano_view.attributions) == 1
    assert kaiano_view.attributions[0].instructor_id is None

    amy_view = await get_instructor_view(db_session, "viewer", slug="amy")
    assert amy_view is not None
    assert len(amy_view.attributions) == 1

    bob_view = await get_instructor_view(db_session, "viewer", slug="bob")
    assert bob_view is not None
    assert len(bob_view.attributions) == 0


async def test_compose_no_instructors_writes_null_instructor_attribution(
    db_session: AsyncSession,
) -> None:
    source, _ = await _seed_source_with_extraction(
        db_session,
        raw_output={"entities": [{"kind": "concept", "name": "frame", "prose": "x"}]},
        instructors_raw=[],
    )
    await compose_source(db_session, source.id)
    await db_session.commit()

    attrs = (
        (
            await db_session.execute(
                select(WcsSourceAttribution).where(
                    WcsSourceAttribution.source_id == source.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(attrs) == 1
    assert attrs[0].instructor_id is None


async def test_resolve_instructor_find_or_create(db_session: AsyncSession) -> None:
    first = await resolve_instructor(db_session, "Kaiano")
    await db_session.commit()
    second = await resolve_instructor(db_session, "Kaiano")
    assert first.id == second.id
