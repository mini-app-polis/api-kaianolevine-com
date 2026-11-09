"""CI as a source of signals: a workflow run that failed, or passed again.

The detector reads the ``workflow_run`` deliveries the org webhook already
brings (routers/notifications.py), independently of what is announced on
Discord. A run in a listed org on a listed branch is a signal, keyed by
repo, workflow and branch, so one workflow failing on one branch is one
streak however many runs fail. Dependabot names each of its runs after its
job, an update ("uv in /. for virtualenv - Update #1648979896") or a
dependency-graph refresh ("Graph Update: uv in /. #1691021664"), so the job
number is dropped from the workflow name first: without that, every run would
open its own streak, and none could ever recover.

``failure`` and ``timed_out`` are failing and remediable, once per streak,
by :func:`remediate`, which picks the remedy from the jobs that failed:

- the dependency audit, failed on a finding: dispatch the fleet's
  ``dependency-fix`` workflow for the repository, which re-locks what the
  advisory flagged and opens a ``fix(deps)`` pull request into ``dev``.
  Re-running would fail the same way. A failed audit on any other branch
  dispatches the same fix, without a streak
  (:func:`fix_audit_off_signal_branches`). An audit job that failed before
  it audited anything (the scanner would not install) is not a finding and
  is re-run like any other failure. A repository with no lock the fixer
  re-locks has no remedy to dispatch, and escalates.
- anything else: re-run the failed jobs, which clears the common case of a
  dependency not yet published. ``action_required`` is failing but not
remediable, since a re-run would wait for the same approval. ``success``
recovers. ``cancelled``, ``skipped``, ``stale`` and ``neutral`` say nothing.
A re-run is never re-run: its run attempt is above one.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from mini_app_polis.logger import get_logger

from ..config import Settings
from .signals import Rule, Signal

logger = get_logger()

KIND = "ci"

_REMEDIABLE = frozenset({"failure", "timed_out"})
_FAILING = _REMEDIABLE | {"action_required"}

GITHUB_API = "https://api.github.com"

#: A failed job whose name contains this is the dependency audit, from the
#: fleet's shared security workflow ("security / dependency audit (python)").
DEPENDENCY_AUDIT_JOB = "dependency audit"

#: The fleet's dependency fixer, in the org's ``.github`` repository. It takes
#: a ``repository`` input naming the one repository to fix.
DEPENDENCY_FIX_WORKFLOW = "dependency-fix.yml"
DEPENDENCY_FIX_REF = "main"

#: The steps of the audit job that report findings: the scan itself
#: ("Audit Python dependencies") and the advisory-database check ("Check Node
#: dependencies against GitHub's advisory database"). A failure in any other
#: step of that job, such as installing the scanner, found nothing.
AUDIT_FINDING_STEPS = ("Audit ", "Check ")

#: The locks the fixer re-locks, read on the branch it fixes. A repository
#: with neither (an npm ``package-lock.json``) is one it cannot fix.
DEPENDENCY_FIX_LOCKS = ("uv.lock", "pnpm-lock.yaml")
DEPENDENCY_FIX_BASE = "dev"

#: The job number Dependabot appends to each run's name: " - Update #N" on
#: an update, " #N" on a dependency-graph refresh.
_DEPENDABOT_JOB = re.compile(r"(?: - Update)? #\d+$")


def workflow_name(name: str) -> str:
    """The workflow's name with any per-run Dependabot job number dropped."""
    return _DEPENDABOT_JOB.sub("", name)


def signal_from_workflow_run(
    payload: dict[str, Any], *, orgs: list[str], branches: list[str]
) -> Signal | None:
    """The signal a ``workflow_run`` delivery carries, or None."""
    run = payload.get("workflow_run")
    repository = payload.get("repository")
    if not isinstance(run, dict) or not isinstance(repository, dict):
        return None
    if payload.get("action") != "completed" or run.get("status") != "completed":
        return None
    full_name = str(repository.get("full_name") or "")
    owner, _, repo = full_name.partition("/")
    branch = str(run.get("head_branch") or "")
    if owner not in orgs or not repo or branch not in branches:
        return None

    conclusion = str(run.get("conclusion") or "")
    workflow = workflow_name(str(run.get("name") or "workflow"))
    key = f"{full_name}/{workflow}@{branch}"
    link = str(run.get("html_url") or "") or None
    if conclusion == "success":
        return Signal(
            kind=KIND,
            key=key,
            state="recovered",
            title=f"{repo}: {workflow} passing on {branch}",
            link=link,
        )
    if conclusion not in _FAILING:
        return None

    attempt = int(run.get("run_attempt") or 1)
    sha = str(run.get("head_sha") or "")[:7]
    subject = str((run.get("head_commit") or {}).get("message") or "").split("\n")[0]
    return Signal(
        kind=KIND,
        key=key,
        state="failing",
        title=f"{repo}: {workflow} failing on {branch}",
        where=repo,
        link=link,
        details=f"{conclusion} on {sha}: {subject}".strip(),
        done_when=f"The {workflow} workflow passes on {branch} again.",
        constraints=(
            "Fix the cause. Do not skip, disable or loosen the check to make it pass."
        ),
        sections=(
            ("Workflow", workflow),
            ("Branch", branch),
            ("Commit", f"{sha} {subject}".strip()),
            ("Attempt", str(attempt)),
        ),
        remediable=conclusion in _REMEDIABLE and attempt == 1,
        context={"repo": full_name, "run_id": run.get("id")},
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "api-kaianolevine-com-signals",
    }


async def _github(
    method: str, path: str, *, token: str, settings: Settings, json: Any = None
) -> httpx.Response | None:
    """One GitHub API call. None when it could not be made at all."""
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_CLIENT_TIMEOUT_SECS) as http:
            return await http.request(
                method, f"{GITHUB_API}{path}", headers=_headers(token), json=json
            )
    except httpx.HTTPError as exc:
        logger.warning("ci signals: %s %s failed: %s", method, path, type(exc).__name__)
        return None


async def failed_jobs(
    repo: str, run_id: int, *, token: str, settings: Settings
) -> list[dict[str, Any]] | None:
    """The run's failed jobs, with their steps, or None if they could not be read."""
    response = await _github(
        "GET",
        f"/repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
        token=token,
        settings=settings,
    )
    if response is None or response.status_code != 200:
        return None
    jobs = response.json().get("jobs") or []
    return [
        job
        for job in jobs
        if isinstance(job, dict) and job.get("conclusion") in _REMEDIABLE
    ]


def audit_found_something(jobs: list[dict[str, Any]]) -> bool:
    """Whether a failed dependency audit among the jobs failed on a finding.

    A failed audit job that lists no steps is taken as a finding, as every
    failed audit was before its steps were read.
    """
    for job in jobs:
        if DEPENDENCY_AUDIT_JOB not in str(job.get("name") or ""):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            return True
        for step in steps:
            if (
                isinstance(step, dict)
                and step.get("conclusion") in _REMEDIABLE
                and str(step.get("name") or "").startswith(AUDIT_FINDING_STEPS)
            ):
                return True
    return False


async def dependency_fix_covers(repo: str, *, token: str, settings: Settings) -> bool:
    """Whether the fixer can re-lock the repository: a lock it knows on its base.

    When a lock's presence cannot be read, the dispatch goes ahead and the
    fixer decides, as before the locks were checked.
    """
    for lock in DEPENDENCY_FIX_LOCKS:
        response = await _github(
            "GET",
            f"/repos/{repo}/contents/{lock}?ref={DEPENDENCY_FIX_BASE}",
            token=token,
            settings=settings,
        )
        if response is None or response.status_code != 404:
            return True
    return False


async def rerun_failed_jobs(
    repo: str, run_id: int, *, token: str, settings: Settings
) -> bool:
    """Ask GitHub to re-run the run's failed jobs. Returns whether it started."""
    response = await _github(
        "POST",
        f"/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs",
        token=token,
        settings=settings,
    )
    if response is None or response.status_code != 201:
        logger.warning(
            "ci signals: re-run of %s/%s refused: %s",
            repo,
            run_id,
            None if response is None else response.status_code,
        )
        return False
    return True


async def dispatch_dependency_fix(repo: str, *, token: str, settings: Settings) -> bool:
    """Start the fleet's dependency fixer for one repository."""
    owner, _, name = repo.partition("/")
    response = await _github(
        "POST",
        f"/repos/{owner}/.github/actions/workflows/{DEPENDENCY_FIX_WORKFLOW}/dispatches",
        token=token,
        settings=settings,
        json={"ref": DEPENDENCY_FIX_REF, "inputs": {"repository": name}},
    )
    if response is None or response.status_code != 204:
        logger.warning(
            "ci signals: dependency-fix dispatch for %s refused: %s",
            repo,
            None if response is None else response.status_code,
        )
        return False
    return True


async def remediate(signal: Signal, settings: Settings) -> bool:
    """Start the remedy the failed jobs call for. Returns whether one started.

    Needs ``GITHUB_ACTIONS_TOKEN``: a token that may run and re-run Actions
    workflows in the org, and nothing more. Without it, or on any refusal,
    the streak escalates instead of waiting. When the failed jobs cannot be
    read, the run is re-run, as before the audit had a remedy of its own.
    """
    token = (settings.GITHUB_ACTIONS_TOKEN or "").strip()
    repo = signal.context.get("repo")
    run_id = signal.context.get("run_id")
    if not token or not isinstance(repo, str) or not isinstance(run_id, int):
        logger.warning("ci signals: cannot remediate %s (no token or run)", signal.key)
        return False

    failed = await failed_jobs(repo, run_id, token=token, settings=settings)
    if failed and audit_found_something(failed):
        if not await dependency_fix_covers(repo, token=token, settings=settings):
            logger.info(
                "ci signals: %s failed its dependency audit, but dependency-fix "
                "cannot re-lock it",
                signal.key,
            )
            return False
        logger.info(
            "ci signals: %s failed its dependency audit; dispatching the fix",
            signal.key,
        )
        return await dispatch_dependency_fix(repo, token=token, settings=settings)
    return await rerun_failed_jobs(repo, run_id, token=token, settings=settings)


#: The branch the dependency fixer pushes its pull request from. Its own
#: failed audit is not dispatched again: the fixer already ran for it.
DEPENDENCY_FIX_BRANCH = "security-fix/deps"


async def fix_audit_off_signal_branches(
    payload: dict[str, Any], settings: Settings
) -> bool:
    """Dispatch the dependency fixer for a failed audit on any other branch.

    Only ``SIGNAL_CI_BRANCHES`` are signals: a failure on ``dev`` or a pull
    request branch opens nothing, since work is expected to break there. A
    failed dependency audit is different. It is not the work's fault, a new
    advisory fails every branch of the repository at once, and the remedy is
    the same wherever it shows: re-lock ``dev``. So it is dispatched from any
    branch, once per first attempt, with no streak and no Issue. The fixer
    opens nothing when ``dev`` is already clean, so a second dispatch for the
    same advisory costs one short run. Returns whether a dispatch started.
    """
    run = payload.get("workflow_run")
    repository = payload.get("repository")
    if not isinstance(run, dict) or not isinstance(repository, dict):
        return False
    if payload.get("action") != "completed" or run.get("status") != "completed":
        return False
    if str(run.get("conclusion") or "") not in _REMEDIABLE:
        return False
    if int(run.get("run_attempt") or 1) != 1:
        return False
    full_name = str(repository.get("full_name") or "")
    owner, _, name = full_name.partition("/")
    branch = str(run.get("head_branch") or "")
    if owner not in settings.SIGNAL_ORGS or not name:
        return False
    if branch in settings.SIGNAL_CI_BRANCHES or branch == DEPENDENCY_FIX_BRANCH:
        return False
    run_id = run.get("id")
    token = (settings.GITHUB_ACTIONS_TOKEN or "").strip()
    if not token or not isinstance(run_id, int):
        return False

    failed = await failed_jobs(full_name, run_id, token=token, settings=settings)
    if not failed or not audit_found_something(failed):
        return False
    if not await dependency_fix_covers(full_name, token=token, settings=settings):
        logger.info(
            "ci signals: %s failed its dependency audit on %s, but dependency-fix "
            "cannot re-lock it",
            full_name,
            branch,
        )
        return False
    logger.info(
        "ci signals: %s failed its dependency audit on %s; dispatching the fix",
        full_name,
        branch,
    )
    return await dispatch_dependency_fix(full_name, token=token, settings=settings)


def rule(settings: Settings) -> Rule:
    """CI's rule: one remedy, then an Issue on the next failure."""
    return Rule(
        escalate_after=settings.SIGNAL_CI_ESCALATE_AFTER,
        remediation=remediate,
        max_remediations=1 if settings.SIGNAL_CI_RERUN else 0,
    )
