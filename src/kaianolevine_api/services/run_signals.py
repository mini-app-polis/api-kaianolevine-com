"""Cog runs as a source of signals: a run that failed, or ran clean again.

The detector reads the run reports cogs already send to ``POST /v1/notify``
(routers/notifications.py), independently of where they are posted on
Discord. A run report is the message ``mini_app_polis.pipeline_status``
renders: an embed titled ``"<repo> · <flow>"`` with a footer that starts
with the run's severity. The severity is the signal, never the text:

- ``ERROR`` and ``CRITICAL`` are failing.
- ``SUCCESS``, ``INFO`` and ``WARN`` recover: the run did its job.

A run is keyed by repo and flow, so one flow failing is one streak however
many of its runs fail. There is no remediation: the next run is the retry,
and two failing runs in a row escalate.

A report from outside production says so in its title (``"[DEVELOPMENT] "``)
and is not a signal, whichever service it reached. Any other message on the
route — no embed, no severity, no ``" · "`` in the title — says nothing.

An idle run that succeeded is not reported at all (pipeline_status logs it
and stops), so a streak recovers on the next run that had something to say
and did not fail.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from .signals import Rule, Signal

KIND = "cog-run"

_FAILING = frozenset({"ERROR", "CRITICAL"})
_RECOVERED = frozenset({"SUCCESS", "INFO", "WARN"})

#: Separates repo from flow in the title, and the fields of the footer.
_SEPARATOR = " · "


def signal_from_report(embeds: list[dict[str, Any]] | None) -> Signal | None:
    """The signal a run report carries, or None."""
    for embed in embeds or []:
        if not isinstance(embed, dict):
            continue
        footer = embed.get("footer")
        footer_text = footer.get("text") if isinstance(footer, dict) else None
        title = embed.get("title")
        if not isinstance(footer_text, str) or not isinstance(title, str):
            continue
        fields = [part.strip() for part in footer_text.split("·")]
        severity = fields[0].upper()
        if severity not in _FAILING | _RECOVERED:
            continue
        if title.startswith("["):
            # Not production: pipeline_status marks every other environment.
            return None
        repo, sep, flow = title.partition(_SEPARATOR)
        repo, flow = repo.strip(), flow.strip()
        if not sep or not repo or not flow:
            continue

        key = f"{repo}/{flow}"
        if severity in _RECOVERED:
            return Signal(kind=KIND, key=key, state="recovered", title=key)

        run_id = fields[-1].removeprefix("run").strip() if len(fields) > 3 else ""
        description = embed.get("description")
        details = description.strip() if isinstance(description, str) else ""
        return Signal(
            kind=KIND,
            key=key,
            state="failing",
            title=f"{repo}: {flow} failing",
            where=repo,
            details=details or None,
            sections=(
                ("Flow", flow),
                ("Severity", severity),
                ("Run", run_id),
            ),
        )
    return None


def rule(settings: Settings) -> Rule:
    """A cog run's rule: no remedy; the next run is the retry."""
    return Rule(escalate_after=settings.SIGNAL_RUN_ESCALATE_AFTER)
