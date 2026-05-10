"""Flat text rendering of WCS notes for embedding.

Pure function with deterministic output for a given input. Bumping
FLATTENER_VERSION invalidates all existing note embeddings via the SHA
mismatch in the convergence flow, which then re-embeds them.

Sections with empty arrays are omitted entirely. Items can be raw strings or
dicts; missing dict keys produce no output for that item.

Rendered sections (in order):
    summary, key_concepts, vocabulary_terms, drills, common_mistakes,
    patterns_and_sequences, student_observations, action_items,
    competition_notes, quotes, references

Excluded by design: off_topic_notes, suggested_new_sections.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

FLATTENER_VERSION = 1


def flatten_note(
    *,
    title: str | None,
    instructors: list[str] | None,
    students: list[str] | None,
    organization: str | None,
    session_date: dt.date | None,
    notes_json: dict[str, Any] | None,
) -> str:
    """Render a WCS note as canonical flat text for embedding."""
    instructors_l = instructors or []
    students_l = students or []
    organization_s = organization or ""
    notes = notes_json or {}

    parts: list[str] = []

    title_clean = (title or "").strip()
    if title_clean:
        parts.append(title_clean)

    parts.append(
        f"{', '.join(instructors_l)} | "
        f"{', '.join(students_l)} | "
        f"{organization_s} | "
        f"{session_date.isoformat() if session_date else ''}"
    )

    summary = (
        (notes.get("summary") or "").strip()
        if isinstance(notes.get("summary"), str)
        else ""
    )
    if summary:
        parts.append(summary)

    sections: list[tuple[str, Any, Callable[[Any], str]]] = [
        ("Key concepts:", notes.get("key_concepts"), _format_key_concept),
        ("Vocabulary:", notes.get("vocabulary_terms"), _format_vocabulary),
        ("Drills:", notes.get("drills"), _format_drill),
        ("Common mistakes:", notes.get("common_mistakes"), _format_common_mistake),
        (
            "Patterns and sequences:",
            notes.get("patterns_and_sequences"),
            _format_pattern,
        ),
        (
            "Student observations:",
            notes.get("student_observations"),
            _format_student_observation,
        ),
        ("Action items:", notes.get("action_items"), _format_action_item),
        (
            "Competition notes:",
            notes.get("competition_notes"),
            _format_competition_note,
        ),
        ("Quotes:", notes.get("quotes"), _format_quote),
        ("References:", notes.get("references"), _format_reference),
    ]

    for header_line, items, formatter in sections:
        rendered = _render_section(header_line, items, formatter)
        if rendered:
            parts.append(rendered)

    return "\n\n".join(parts)


def _render_section(header: str, items: Any, formatter: Callable[[Any], str]) -> str:
    if not isinstance(items, list) or not items:
        return ""
    bullets = [b for b in (formatter(i) for i in items) if b]
    if not bullets:
        return ""
    return header + "\n" + "\n".join(bullets)


def _str_field(item: Any, key: str) -> str:
    if not isinstance(item, dict):
        return ""
    val = item.get(key)
    if not isinstance(val, str):
        return ""
    return val.strip()


def _format_key_concept(item: Any) -> str:
    if isinstance(item, str):
        s = item.strip()
        return f"- {s}" if s else ""
    concept = _str_field(item, "concept")
    if not concept:
        return ""
    detail = _str_field(item, "detail")
    return f"- {concept} — {detail}" if detail else f"- {concept}"


def _format_vocabulary(item: Any) -> str:
    term = _str_field(item, "term")
    if not term:
        return ""
    definition = _str_field(item, "definition")
    return f"- {term}: {definition}" if definition else f"- {term}"


def _format_drill(item: Any) -> str:
    name = _str_field(item, "name")
    if not name:
        return ""
    goal = _str_field(item, "goal")
    head = f"- {name} — {goal}" if goal else f"- {name}"
    if isinstance(item, dict):
        steps = item.get("steps")
        if isinstance(steps, list):
            steps_clean = [s.strip() for s in steps if isinstance(s, str) and s.strip()]
            if steps_clean:
                return f"{head}\n  Steps: {'; '.join(steps_clean)}"
    return head


def _format_common_mistake(item: Any) -> str:
    mistake = _str_field(item, "mistake")
    if not mistake:
        return ""
    correction = _str_field(item, "correction")
    return f"- {mistake} → {correction}" if correction else f"- {mistake}"


def _format_pattern(item: Any) -> str:
    name = _str_field(item, "name")
    if not name:
        return ""
    description = _str_field(item, "description")
    return f"- {name}: {description}" if description else f"- {name}"


def _format_student_observation(item: Any) -> str:
    if isinstance(item, str):
        s = item.strip()
        return f"- {s}" if s else ""
    obs = _str_field(item, "observation")
    return f"- {obs}" if obs else ""


def _format_action_item(item: Any) -> str:
    action = _str_field(item, "action")
    if not action:
        return ""
    rationale = _str_field(item, "rationale")
    return f"- {action}: {rationale}" if rationale else f"- {action}"


def _format_competition_note(item: Any) -> str:
    note = _str_field(item, "note")
    if not note:
        return ""
    context = _str_field(item, "context")
    return f"- {note} ({context})" if context else f"- {note}"


def _format_quote(item: Any) -> str:
    quote = _str_field(item, "quote")
    if not quote:
        return ""
    speaker = _str_field(item, "speaker")
    context = _str_field(item, "context")
    if speaker and context:
        return f'- "{quote}" — {speaker} ({context})'
    if speaker:
        return f'- "{quote}" — {speaker}'
    if context:
        return f'- "{quote}" ({context})'
    return f'- "{quote}"'


def _format_reference(item: Any) -> str:
    name = _str_field(item, "name")
    if not name:
        return ""
    type_ = _str_field(item, "type")
    context = _str_field(item, "context")
    if type_ and context:
        return f"- {name} ({type_}): {context}"
    if type_:
        return f"- {name} ({type_})"
    if context:
        return f"- {name}: {context}"
    return f"- {name}"
