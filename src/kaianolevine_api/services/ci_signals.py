"""CI as a source of signals: a workflow run that failed, or passed again.

The detector reads the ``workflow_run`` deliveries the org webhook already
brings (routers/notifications.py), independently of what is announced on
Discord. A run in a listed org on a listed branch is a signal, keyed by
repo, workflow and branch, so one workflow failing on one branch is one
streak however many runs fail. Dependabot names each update run after its
job ("uv in /. for virtualenv - Update #1648979896"), so the job number is
dropped from the workflow name first: without that, every run would open
its own streak, and none could ever recover.

``failure`` and ``timed_out`` are failing and remediable, once per streak,
by :func:`remediate`, which picks the remedy from the jobs that failed:

- the dependency audit: dispatch the fleet's ``dependency-fix`` workflow for
  the repository, which re-locks what the advisory flagged and opens a
  ``fix(deps)`` pull request into ``dev``. Re-running would fail the same way.
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

#: The job number Dependabot appends to each update run's name.
_DEPENDABOT_JOB = re.compile(r" - Update #\d+$")


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


async def failed_job_names(
    repo: str, run_id: int, *, token: str, settings: Settings
) -> list[str] | None:
    """The names of the run's failed jobs, or None if they could not be read."""
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
        str(job.get("name") or "")
        for job in jobs
        if isinstance(job, dict) and job.get("conclusion") in _REMEDIABLE
    ]


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

    failed = await failed_job_names(repo, run_id, token=token, settings=settings)
    if failed and any(DEPENDENCY_AUDIT_JOB in name for name in failed):
        logger.info(
            "ci signals: %s failed its dependency audit; dispatching the fix",
            signal.key,
        )
        return await dispatch_dependency_fix(repo, token=token, settings=settings)
    return await rerun_failed_jobs(repo, run_id, token=token, settings=settings)


def rule(settings: Settings) -> Rule:
    """CI's rule: one remedy, then a task on the next failure."""
    return Rule(
        escalate_after=settings.SIGNAL_CI_ESCALATE_AFTER,
        remediation=remediate,
        max_remediations=1 if settings.SIGNAL_CI_RERUN else 0,
    )
