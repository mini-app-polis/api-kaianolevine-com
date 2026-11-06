"""How a conformance Issue names what was graded, against what, by what."""

from __future__ import annotations

from kaianolevine_api.services import conformance_signals
from kaianolevine_api.services.conformance_signals import Versions

REPO = "api-kaianolevine-com"


def test_all_three_versions_are_named() -> None:
    versions = Versions(
        repo_version="v1.43.0",
        repo_commit="abc1234def5678",
        evaluator_version="4.2.0",
        standards_version="6.20.0",
    )
    assert versions.line(REPO) == (
        f"{REPO} v1.43.0 (abc1234), evaluator-cog 4.2.0, standards 6.20.0"
    )


def test_only_what_the_row_has_is_named() -> None:
    assert Versions(standards_version="6.20.0").line(REPO) == "standards 6.20.0"
    assert Versions(repo_commit="abc1234def").line(REPO) == f"{REPO} (abc1234)"
    assert Versions(repo_version="main").line(REPO) == f"{REPO} main"
    assert Versions().line(REPO) == ""


def test_a_finding_carries_its_versions_as_evidence() -> None:
    signal = conformance_signals.failing_signal(
        repo=REPO,
        source="conformance_deterministic",
        severity="WARN",
        violation_id="DOC-004",
        finding="f",
        suggestion=None,
        versions=Versions(repo_version="v1.43.0", evaluator_version="4.2.0"),
    )
    assert signal is not None
    assert signal.evidence == (("Evaluated", f"{REPO} v1.43.0, evaluator-cog 4.2.0"),)


def test_a_finding_without_versions_has_no_evidence() -> None:
    signal = conformance_signals.failing_signal(
        repo=REPO,
        source="conformance_deterministic",
        severity="WARN",
        violation_id="DOC-004",
        finding="f",
        suggestion=None,
    )
    assert signal is not None
    assert signal.evidence == ()


def test_every_severity_but_success_is_a_failing_signal() -> None:
    for severity in ("INFO", "WARN", "ERROR", "CRITICAL"):
        signal = conformance_signals.failing_signal(
            repo=REPO,
            source="conformance_deterministic",
            severity=severity,
            violation_id="LAYOUT-002",
            finding="Not in the written layout for infrastructure.",
            suggestion=None,
        )
        assert signal is not None, severity
        assert signal.key == f"{REPO}/conformance_deterministic/LAYOUT-002"
    assert (
        conformance_signals.failing_signal(
            repo=REPO,
            source="conformance_deterministic",
            severity="SUCCESS",
            violation_id="STATUS",
            finding="passed",
            suggestion=None,
        )
        is None
    )
