"""CI as a source of signals: a workflow run that failed, or passed again.

The detector reads the ``workflow_run`` deliveries the org webhook already
brings (routers/notifications.py), independently of what is announced on
Discord. A run in a listed org on a listed branch is a signal, keyed by
repo, workflow and branch, so one workflow failing on one branch is one
streak however many runs fail.

``failure`` and ``timed_out`` are failing and remediable: the first one is
re-run once (:func:`rerun_failed_jobs`), which clears the common case of a
dependency not yet published. ``action_required`` is failing but not
remediable, since a re-run would wait for the same approval. ``success``
recovers. ``cancelled``, ``skipped``, ``stale`` and ``neutral`` say nothing.
A re-run is never re-run: its run attempt is above one.
"""

from __future__ import annotations

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
    workflow = str(run.get("name") or "workflow")
    key = f"{full_name}/{workflow}@{branch}"
    if conclusion == "success":
        return Signal(kind=KIND, key=key, state="recovered", title=key)
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
        link=str(run.get("html_url") or "") or None,
        details=f"{conclusion} on {sha}: {subject}".strip(),
        sections=(
            ("Workflow", workflow),
            ("Branch", branch),
            ("Commit", f"{sha} {subject}".strip()),
            ("Attempt", str(attempt)),
        ),
        remediable=conclusion in _REMEDIABLE and attempt == 1,
        context={"repo": full_name, "run_id": run.get("id")},
    )


async def rerun_failed_jobs(signal: Signal, settings: Settings) -> bool:
    """Ask GitHub to re-run the run's failed jobs. Returns whether it started.

    Needs ``GITHUB_ACTIONS_TOKEN``: a token that may re-run Actions jobs in
    the org, and nothing more. Without it, or on any refusal, the streak
    escalates instead of waiting.
    """
    token = (settings.GITHUB_ACTIONS_TOKEN or "").strip()
    repo = signal.context.get("repo")
    run_id = signal.context.get("run_id")
    if not token or not repo or not isinstance(run_id, int):
        logger.warning("ci signals: cannot re-run %s (no token or run)", signal.key)
        return False
    url = f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs"
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_CLIENT_TIMEOUT_SECS) as http:
            response = await http.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "api-kaianolevine-com-signals",
                },
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "ci signals: re-run of %s failed: %s", signal.key, type(exc).__name__
        )
        return False
    if response.status_code != 201:
        logger.warning(
            "ci signals: re-run of %s refused: %s", signal.key, response.status_code
        )
        return False
    return True


def rule(settings: Settings) -> Rule:
    """CI's rule: one re-run, then a task on the next failure."""
    return Rule(
        escalate_after=settings.SIGNAL_CI_ESCALATE_AFTER,
        remediation=rerun_failed_jobs,
        max_remediations=1 if settings.SIGNAL_CI_RERUN else 0,
    )
