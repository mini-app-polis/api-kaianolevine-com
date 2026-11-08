"""The CI detector: its streak key, and which remedy a failed run gets.

Dependabot names every update run after its job, so two failing runs of
the same update would be two workflows unless the job number is dropped.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from kaianolevine_api.config import Settings
from kaianolevine_api.services import ci_signals

ORGS = ["mini-app-polis"]
BRANCHES = ["main", "dev"]


def _run(conclusion: str, name: str, *, branch: str = "main") -> dict:
    return {
        "action": "completed",
        "repository": {"full_name": "mini-app-polis/db-ops"},
        "workflow_run": {
            "id": 1,
            "name": name,
            "status": "completed",
            "conclusion": conclusion,
            "head_branch": branch,
            "head_sha": "c" * 40,
            "head_commit": {"message": "chore(release): 1.0.0"},
            "html_url": "https://github.com/mini-app-polis/db-ops/actions/runs/1",
            "run_attempt": 1,
        },
    }


def _signal(conclusion: str, name: str, **kwargs) -> ci_signals.Signal:
    signal = ci_signals.signal_from_workflow_run(
        _run(conclusion, name, **kwargs), orgs=ORGS, branches=BRANCHES
    )
    assert signal is not None
    return signal


def test_repeated_dependabot_update_runs_share_one_streak() -> None:
    first = _signal("failure", "uv in /. for virtualenv - Update #1648979896")
    second = _signal("failure", "uv in /. for virtualenv - Update #1649050511")

    expected = "mini-app-polis/db-ops/uv in /. for virtualenv@main"
    assert first.key == second.key == expected
    assert first.title == "db-ops: uv in /. for virtualenv failing on main"
    assert first.done_when == (
        "The uv in /. for virtualenv workflow passes on main again."
    )


def test_a_later_dependabot_success_recovers_the_streak() -> None:
    failed = _signal("failure", "uv in /. for virtualenv - Update #1648979896")
    passed = _signal("success", "uv in /. for virtualenv - Update #1649999999")

    assert passed.state == "recovered"
    assert passed.key == failed.key


def test_different_packages_stay_separate_streaks() -> None:
    virtualenv = _signal("failure", "uv in /. for virtualenv - Update #1")
    filelock = _signal("failure", "uv in /. for filelock - Update #2")

    assert virtualenv.key != filelock.key


@pytest.mark.parametrize(
    "name", ["CI", "dependency-fix", "terraform", "Release - Update notes"]
)
def test_fixed_workflow_names_are_unchanged(name: str) -> None:
    assert _signal("failure", name, branch="dev").key == (
        f"mini-app-polis/db-ops/{name}@dev"
    )


def test_by_default_only_main_failures_are_signals() -> None:
    branches = Settings.model_fields["SIGNAL_CI_BRANCHES"].default

    on_dev = ci_signals.signal_from_workflow_run(
        _run("failure", "CI", branch="dev"), orgs=ORGS, branches=branches
    )
    on_main = ci_signals.signal_from_workflow_run(
        _run("failure", "CI", branch="main"), orgs=ORGS, branches=branches
    )

    assert on_dev is None
    assert on_main is not None
    assert on_main.state == "failing"


# ── which failed audits are findings ──────────────────────────────────────


def _audit(*steps: tuple[str, str]) -> dict:
    return {
        "name": "security / dependency audit (node)",
        "conclusion": "failure",
        "steps": [{"name": n, "conclusion": c} for n, c in steps],
    }


@pytest.mark.parametrize(
    "step",
    [
        "Audit Node dependencies",
        "Check Node dependencies against GitHub's advisory database",
    ],
)
def test_an_audit_that_failed_on_its_scan_is_a_finding(step: str) -> None:
    job = _audit(("Install osv-scanner", "success"), (step, "failure"))
    assert ci_signals.audit_found_something([job]) is True


@pytest.mark.parametrize(
    "step", ["Install osv-scanner", "Install gitleaks", "Set up uv"]
)
def test_an_audit_that_failed_before_scanning_is_not_a_finding(step: str) -> None:
    job = _audit((step, "failure"), ("Audit Node dependencies", "skipped"))
    assert ci_signals.audit_found_something([job]) is False


def test_an_audit_with_no_steps_listed_is_taken_as_a_finding() -> None:
    job = {"name": "security / dependency audit (python)", "conclusion": "failure"}
    assert ci_signals.audit_found_something([job]) is True


def test_other_failed_jobs_are_not_findings() -> None:
    job = {
        "name": "security / secret scan",
        "conclusion": "failure",
        "steps": [{"name": "Audit history", "conclusion": "failure"}],
    }
    assert ci_signals.audit_found_something([job]) is False


# ── whether the fixer can re-lock the repository ──────────────────────────


GH = "https://api.github.com/repos/mini-app-polis/db-ops"


def _settings() -> Settings:
    return Settings(GITHUB_ACTIONS_TOKEN="gh-test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uv", "pnpm", "expected"),
    [(200, 404, True), (404, 200, True), (404, 404, False), (502, 404, True)],
)
async def test_the_fixer_covers_a_repository_with_a_lock_it_knows(
    uv: int, pnpm: int, expected: bool
) -> None:
    with respx.mock:
        respx.get(f"{GH}/contents/uv.lock").mock(return_value=Response(uv))
        respx.get(f"{GH}/contents/pnpm-lock.yaml").mock(return_value=Response(pnpm))
        covers = await ci_signals.dependency_fix_covers(
            "mini-app-polis/db-ops", token="gh-test", settings=_settings()
        )
    assert covers is expected


@pytest.mark.asyncio
async def test_a_scanner_that_would_not_install_is_re_run_not_fixed() -> None:
    jobs = {"jobs": [_audit(("Install osv-scanner", "failure"))]}
    with respx.mock:
        respx.get(url__startswith=f"{GH}/actions/runs/1/jobs").mock(
            return_value=Response(200, json=jobs)
        )
        rerun = respx.post(f"{GH}/actions/runs/1/rerun-failed-jobs").mock(
            return_value=Response(201)
        )
        started = await ci_signals.remediate(_signal("failure", "CI"), _settings())
    assert started is True and rerun.called


@pytest.mark.asyncio
async def test_a_finding_the_fixer_cannot_re_lock_escalates() -> None:
    jobs = {"jobs": [_audit(("Audit Node dependencies", "failure"))]}
    with respx.mock:
        respx.get(url__startswith=f"{GH}/actions/runs/1/jobs").mock(
            return_value=Response(200, json=jobs)
        )
        respx.get(url__startswith=f"{GH}/contents/").mock(return_value=Response(404))
        dispatch = respx.post(
            url__startswith="https://api.github.com/repos/mini-app-polis/.github/"
        ).mock(return_value=Response(204))
        started = await ci_signals.remediate(_signal("failure", "CI"), _settings())
    assert started is False and not dispatch.called
