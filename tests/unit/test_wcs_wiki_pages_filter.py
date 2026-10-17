"""Filtering a page document for one reader (wiki-curator-cog ADR-006, MINI-96).

One document is stored per page. These pin what a reader who cannot see a
source is given: none of its evidence, none of its links, and no page at all
when every source behind the prose is hidden.
"""

from __future__ import annotations

import uuid

from kaianolevine_api.schemas import WcsWikiPage
from kaianolevine_api.services.wcs_wiki_pages import (
    filter_page,
    source_ids,
    strip_hidden_links,
)

PUBLIC = uuid.UUID("00000000-0000-0000-0000-00000000000a")
PRIVATE = uuid.UUID("00000000-0000-0000-0000-00000000000b")


def _cite(source: uuid.UUID, public: bool) -> dict:
    return {
        "source_id": str(source),
        "source_slug": f"2026-10-01-{str(source)[-1]}",
        "session_date": "2026-10-01",
        "public": public,
    }


def _page(**over) -> WcsWikiPage:
    raw = {
        "slug": "anchor",
        "name": "Anchor",
        "kind": "concept",
        "definition": f"See [[source:{PRIVATE}|the lesson]] and [[connection]].",
        "in_practice": f"Taught in [[source:{PUBLIC}|class]] and [[source:{PRIVATE}]].",
        "details": [
            {
                "slug": "x",
                "name": "x",
                "gist": f"per [[source:{PRIVATE}|y]]",
                "depth": 0,
            }
        ],
        "by_teacher": [
            {
                "name": "Kaiano",
                "statements": [
                    {
                        "kind": "attribution",
                        "text": "open",
                        "citation": _cite(PUBLIC, True),
                    },
                    {
                        "kind": "definition",
                        "text": "hidden",
                        "citation": _cite(PRIVATE, True),
                    },
                ],
            },
            {
                "name": "Robert",
                "statements": [
                    {
                        "kind": "attribution",
                        "text": "hidden",
                        "citation": _cite(PRIVATE, True),
                    }
                ],
            },
        ],
        "mistakes": [{"text": "m", "citation": _cite(PRIVATE, True)}],
        "develops": [{"skill": "s", "citation": _cite(PUBLIC, True)}],
        "related": [
            {"slug": "frame", "kind": "concept", "name": "Frame", "relation": "r"},
            {
                "slug": "lead",
                "kind": "concept",
                "name": "Lead",
                "relation": "r",
                "citation": _cite(PRIVATE, True),
            },
        ],
        "sources": [_cite(PUBLIC, True), _cite(PRIVATE, True)],
        "evidence_sources": [str(PUBLIC), str(PRIVATE)],
    }
    raw.update(over)
    return WcsWikiPage.model_validate(raw)


def test_the_admin_sees_everything_marked_by_current_visibility() -> None:
    page = filter_page(_page(), None, {PUBLIC})
    assert page is not None
    assert [s.text for t in page.by_teacher for s in t.statements] == [
        "open",
        "hidden",
        "hidden",
    ]
    # Stored as public; the source is private now, and the read says so.
    assert {c.source_id: c.public for c in page.sources} == {
        PUBLIC: True,
        PRIVATE: False,
    }
    assert f"source:{PRIVATE}" in page.definition


def test_a_reader_gets_only_the_evidence_they_can_see() -> None:
    page = filter_page(_page(), {PUBLIC}, {PUBLIC})
    assert page is not None
    assert [t.name for t in page.by_teacher] == ["Kaiano"]
    assert [s.text for s in page.by_teacher[0].statements] == ["open"]
    assert page.mistakes == []
    assert [s.skill for s in page.develops] == ["s"]
    assert [r.slug for r in page.related] == ["frame"]
    assert [c.source_id for c in page.sources] == [PUBLIC]
    assert page.evidence_sources == [PUBLIC]
    assert page.definition == "See the lesson and [[connection]]."
    assert page.in_practice == f"Taught in [[source:{PUBLIC}|class]] and ."
    assert page.details[0].gist == "per y"


def test_a_page_built_only_from_hidden_sources_is_not_served() -> None:
    page = _page(evidence_sources=[str(PRIVATE)])
    assert filter_page(page, {PUBLIC}, {PUBLIC}) is None
    assert filter_page(page, None, set()) is not None


def test_a_page_with_no_evidence_is_served() -> None:
    assert filter_page(_page(evidence_sources=[]), set(), set()) is not None


def test_filtering_does_not_change_the_stored_document() -> None:
    page = _page()
    filter_page(page, {PUBLIC}, {PUBLIC})
    assert len(page.by_teacher) == 2


def test_every_named_source_is_collected() -> None:
    assert source_ids(_page()) == {PUBLIC, PRIVATE}


def test_links_are_left_alone_for_the_admin() -> None:
    text = f"[[source:{PRIVATE}|x]]"
    assert strip_hidden_links(text, None) == text
