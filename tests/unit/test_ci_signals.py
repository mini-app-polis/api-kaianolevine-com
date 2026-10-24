"""The CI detector's streak key: one per workflow, repository and branch.

Dependabot names every update run after its job, so two failing runs of
the same update would be two workflows unless the job number is dropped.
"""

from __future__ import annotations

import pytest

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
