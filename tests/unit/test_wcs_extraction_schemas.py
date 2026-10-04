"""Pydantic validation tests for WCS extraction payload schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kaianolevine_api.schemas import (
    WcsExtractionDrillPurpose,
    WcsExtractionEntity,
    WcsExtractionEntityRelation,
    WcsExtractionRawOutput,
)


def test_raw_output_minimal_payload() -> None:
    out = WcsExtractionRawOutput.model_validate({})
    assert out.title == ""
    assert out.entities == []
    assert out.references == []


def test_raw_output_rich_payload() -> None:
    payload = {
        "title": "Frame lesson",
        "summary": "Worked on frame.",
        "entities": [{"kind": "concept", "name": "frame", "prose": "Stay connected."}],
        "entity_relations": [
            {
                "from": "frame",
                "to": "connection",
                "relation_kind": "concept_contains_concept",
            }
        ],
        "references": [{"name": "Ben Morris", "type": "pro"}],
    }
    out = WcsExtractionRawOutput.model_validate(payload)
    assert out.entities[0].name == "frame"
    assert out.entity_relations[0].from_ == "frame"
    assert out.references[0].type == "pro"


def test_entity_kind_enforced() -> None:
    with pytest.raises(ValidationError):
        WcsExtractionEntity.model_validate({"kind": "skill", "name": "frame"})


def test_entity_relation_from_alias() -> None:
    rel = WcsExtractionEntityRelation.model_validate(
        {
            "from": "anchor step",
            "to": "slot",
            "relation_kind": "concept_informs_technique",
        }
    )
    assert rel.from_ == "anchor step"
    dumped = rel.model_dump(by_alias=True)
    assert dumped["from"] == "anchor step"


def test_schema_accepts_long_skill_description_that_previously_failed() -> None:
    """Regression test for the Robert Royston 2025-06-28 case: a 161-char
    skill description is now valid at the API layer (was failing with
    Pydantic max_length=120 even after the cog's schema accepted it).
    """
    payload = WcsExtractionRawOutput(
        entities=[],
        drill_purposes=[
            WcsExtractionDrillPurpose(
                drill_name="second-step push drill",
                skill_description=(
                    "Control of the first step and half rotation with no "
                    "collection; ability to maintain the push through the "
                    "second step without collecting."
                ),
                focus_context="",
            )
        ],
    )
    # Must not raise. The skill description is 161 chars; previously
    # capped at 120.
    assert len(payload.drill_purposes[0].skill_description) > 120


def test_raw_output_extra_allow() -> None:
    out = WcsExtractionRawOutput.model_validate(
        {"title": "t", "future_field": {"nested": True}}
    )
    assert out.title == "t"
    dumped = out.model_dump()
    assert dumped["future_field"] == {"nested": True}
