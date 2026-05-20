"""Smoke tests for WCS entity substrate ORM models (migration 019)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.models import (
    WcsEntity,
    WcsEntityAlias,
    WcsInstructor,
    WcsInstructorAlias,
    WcsSource,
    WcsSourceAttribution,
    WcsSourceExtraction,
    WcsTranscript,
)


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        yield session


async def test_substrate_models_instantiate_and_relationships(
    db_session: AsyncSession,
) -> None:
    transcript = WcsTranscript(
        owner_id="dev-owner",
        raw_text="Lesson about anchor step.",
        source_type="plaud",
        source_filename="lesson.txt",
        drive_file_id="drive-1",
    )
    db_session.add(transcript)
    await db_session.flush()

    source = WcsSource(
        owner_id="dev-owner",
        transcript_id=transcript.id,
        title="Anchor lesson",
        instructors_raw=["Kaiano"],
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
        raw_output={"entities": [], "title": "Anchor"},
    )
    db_session.add(extraction)

    instructor = WcsInstructor(slug="kaiano", canonical_name="Kaiano")
    db_session.add(instructor)
    await db_session.flush()
    db_session.add(
        WcsInstructorAlias(
            instructor_id=instructor.id,
            alias="kaiano",
            origin="extraction",
        )
    )

    entity = WcsEntity(
        slug="anchor-step",
        canonical_name="Anchor Step",
        kind="technique",
        external_origin={"domain": "wcs"},
    )
    db_session.add(entity)
    await db_session.flush()
    db_session.add(
        WcsEntityAlias(entity_id=entity.id, alias="anchor-step", origin="extraction")
    )

    db_session.add(
        WcsSourceAttribution(
            source_id=source.id,
            entity_id=entity.id,
            instructor_id=instructor.id,
            attribution_kind="taught",
            prose="Keep your anchor.",
            raw_term="anchor step",
            drill_steps=["step 1", "step 2"],
        )
    )
    await db_session.commit()

    loaded = await db_session.get(WcsSource, source.id)
    assert loaded is not None
    await db_session.refresh(loaded, ["transcript", "extractions"])
    assert loaded.transcript.id == transcript.id
    assert len(loaded.extractions) == 1
    assert loaded.extractions[0].raw_output["title"] == "Anchor"

    ent = await db_session.get(WcsEntity, entity.id)
    assert ent is not None
    await db_session.refresh(ent, ["aliases", "attributions"])
    assert ent.external_origin == {"domain": "wcs"}
    assert len(ent.aliases) == 1
    assert len(ent.attributions) == 1
    assert ent.attributions[0].drill_steps == ["step 1", "step 2"]

    inst = await db_session.get(WcsInstructor, instructor.id)
    assert inst is not None
    await db_session.refresh(inst, ["aliases"])
    assert len(inst.aliases) == 1

    tx = await db_session.get(WcsTranscript, transcript.id)
    assert tx is not None
    await db_session.refresh(tx, ["sources"])
    assert len(tx.sources) == 1


def test_legacy_wcs_note_table_name() -> None:
    from kaianolevine_api.models import LegacyWcsNote

    assert LegacyWcsNote.__tablename__ == "_legacy_wcs_notes"
