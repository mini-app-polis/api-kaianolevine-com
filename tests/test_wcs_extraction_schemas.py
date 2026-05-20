"""Pydantic validation tests for WCS extraction payload schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kaianolevine_api.schemas import (
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


def test_name_length_caps() -> None:
    with pytest.raises(ValidationError):
        WcsExtractionEntity.model_validate({"kind": "concept", "name": "x" * 81})


def test_raw_output_extra_allow() -> None:
    out = WcsExtractionRawOutput.model_validate(
        {"title": "t", "future_field": {"nested": True}}
    )
    assert out.title == "t"
    dumped = out.model_dump()
    assert dumped["future_field"] == {"nested": True}
