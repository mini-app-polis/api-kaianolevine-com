"""The digest the evaluations table deduplicates by (PIPE-002).

The expected values below were produced by **PostgreSQL**, not by the
function under test. Migration 030 backfills pre-existing rows with the SQL
equivalent of this function, and the unique index compares what Python
writes against what that migration wrote — so the two must agree byte for
byte, or old rows are hashed one way and new rows another and the index
stops recognising a redelivery as the finding it already holds.

This suite runs on SQLite and cannot execute the SQL, so Postgres's answer
is pinned here instead. To regenerate:

    SELECT encode(sha256(convert_to(
             coalesce(violation_id, '') || E'\\x1F' ||
             coalesce(dimension,    '') || E'\\x1F' ||
             coalesce(severity,     '') || E'\\x1F' ||
             coalesce(finding,      '') || E'\\x1F' ||
             coalesce(suggestion,   ''), 'UTF8')), 'hex');
"""

from __future__ import annotations

from kaianolevine_api.services.evaluation_fingerprint import evaluation_fingerprint


def test_digest_matches_what_postgres_produces() -> None:
    """Change the function and this fails, which is the point."""
    assert (
        evaluation_fingerprint(
            violation_id="CD-026",
            dimension="delivery",
            severity="ERROR",
            finding='job "evaluate" is missing',
            suggestion="add it",
        )
        == "daa81177507731df0f6e506227f1cb7cc53dca74a34f82403476c8ba2e5ab05f"
    )


def test_digest_matches_postgres_on_non_ascii_text() -> None:
    """Findings carry em dashes and accented names; UTF-8 on both sides."""
    assert (
        evaluation_fingerprint(
            violation_id=None,
            dimension="structural_conformance",
            severity="SUCCESS",
            finding="passed — naïve ünïcode ✓",
            suggestion=None,
        )
        == "f3f7790f487057ff39505a809de4571400aa64251b96f408c5cc033752cecc37"
    )


def test_digest_matches_postgres_when_every_field_is_null() -> None:
    """Postgres coalesces NULL to ''; so does this. Four separators, no text."""
    assert (
        evaluation_fingerprint(
            violation_id=None,
            dimension=None,
            severity=None,
            finding=None,
            suggestion=None,
        )
        == "10e7fb50515179ec39dc8dec4958a936e4efad045cc441d1698cfb4783870386"
    )


def test_a_null_and_an_empty_string_are_the_same_finding() -> None:
    """Deliberate, and it has to match the migration's coalesce.

    A finding with no suggestion and one with an empty suggestion are the
    same finding. Treating them differently would let a client store the
    same thing twice by sending "" where it previously sent nothing.
    """
    common = {
        "violation_id": "SEC-002",
        "dimension": "security_posture",
        "severity": "WARN",
        "finding": "a finding",
    }
    assert evaluation_fingerprint(**common, suggestion=None) == evaluation_fingerprint(
        **common, suggestion=""
    )


def test_fields_cannot_bleed_across_the_separator() -> None:
    """Moving a character over a field boundary must change the digest.

    Concatenating without a separator would hash ("AB", "C") and ("A",
    "BC") alike — two distinct findings collapsing into one stored row,
    which is a silently dropped finding.
    """
    left = evaluation_fingerprint(
        violation_id="AB",
        dimension="C",
        severity="ERROR",
        finding="f",
        suggestion="s",
    )
    right = evaluation_fingerprint(
        violation_id="A",
        dimension="BC",
        severity="ERROR",
        finding="f",
        suggestion="s",
    )
    assert left != right
