"""Conformance findings as a source of signals.

The evaluator posts its findings to ``POST /v1/evaluations`` one row at a
time. A finding at WARN or worse from a conformance source is a failing
signal, keyed ``<repo>/<source>/<rule>``, so one rule failing in one repo for
one source is one streak however many runs report it; it escalates on first
sight, and each further run that reports it comments on its Issue. Deferred
and downgraded findings arrive at INFO and exempted ones not at all, so the
severity alone keeps them out.

Only a complete, clean evaluation is proof that a finding cleared: the
evaluator posts a SUCCESS row for a repo and source only when that
evaluation found nothing. That row recovers every open streak for the repo
and source. A run that still reports other findings, or one that did not
finish, closes nothing.

Every Issue names what was graded, against what and by what, where the row
says: the application's version and commit, the standards version and the
evaluator-cog version. The Issue and each further comment name them for the
finding; the comment that closes it names them for the clean evaluation.

The evaluator's own STATUS rows say a repo was not evaluated, or not
assessed against the LLM checks. They are about the evaluator's run, not
about the repo, so their Issues open in evaluator-cog, one per repo, source
and kind, and the same SUCCESS row closes them.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from . import signals
from .signals import Outcome, Rule, Signal

KIND = "conformance"
STATUS_KIND = "evaluator-status"

#: The evaluation sources whose findings are conformance to a rule.
SOURCES: dict[str, str] = {
    "conformance_deterministic": "deterministic",
    "conformance_llm": "LLM",
}

_FAILING = frozenset({"WARN", "ERROR", "CRITICAL"})

#: The id the evaluator posts a statement about its own run under.
STATUS_RULE = "STATUS"

#: Where an Issue about the evaluator's run opens.
EVALUATOR_REPO = "evaluator-cog"

#: The streak kinds a clean evaluation closes.
_KINDS = (KIND, STATUS_KIND)


@dataclass(frozen=True)
class Versions:
    """What an evaluation row says was graded, against what, by what."""

    repo_version: str | None = None
    repo_commit: str | None = None
    evaluator_version: str | None = None
    standards_version: str | None = None

    def line(self, repo: str) -> str:
        """``<repo> <version> (<commit>), evaluator-cog <v>, standards <v>``.

        Each part only where the row has it; empty when it has none.
        """
        parts: list[str] = []
        application = " ".join(p for p in (repo, self.repo_version or "") if p)
        if self.repo_version or self.repo_commit:
            commit = (self.repo_commit or "")[:7]
            parts.append(f"{application} ({commit})" if commit else application)
        if self.evaluator_version:
            parts.append(f"evaluator-cog {self.evaluator_version}")
        if self.standards_version:
            parts.append(f"standards {self.standards_version}")
        return ", ".join(parts)


def status_kind(finding: str) -> str:
    """Which statement about the run a STATUS row makes."""
    if "not assessed" in finding:
        return "not-assessed"
    if "not evaluated" in finding:
        return "not-evaluated"
    return "status"


def failing_signal(
    *,
    repo: str,
    source: str,
    severity: str,
    violation_id: str | None,
    finding: str | None,
    suggestion: str | None,
    versions: Versions | None = None,
) -> Signal | None:
    """The failing signal one stored row carries, or None."""
    label = SOURCES.get(source)
    if label is None or severity not in _FAILING or not violation_id:
        return None
    finding = (finding or "").strip()
    sections = (("Severity", severity), ("Source", source))
    evaluated = (versions or Versions()).line(repo)
    evidence = (("Evaluated", evaluated),) if evaluated else ()
    if violation_id == STATUS_RULE:
        kind = status_kind(finding)
        return Signal(
            kind=STATUS_KIND,
            key=f"{repo}/{source}/{kind}",
            state="failing",
            title=f"{repo}: {kind.replace('-', ' ')} in the {label} conformance run",
            where=EVALUATOR_REPO,
            details=finding or None,
            done_when=(
                f"A complete {label} conformance evaluation of {repo} finishes "
                "and comes back clean."
            ),
            constraints=(
                "Fix why the run did not finish. A repo that was not evaluated "
                "is unknown, not clean."
            ),
            sections=sections,
            evidence=evidence,
        )
    return Signal(
        kind=KIND,
        key=f"{repo}/{source}/{violation_id}",
        state="failing",
        title=f"{repo}: {violation_id} ({label} conformance)",
        where=repo,
        details=finding or None,
        done_when=(
            f"A complete {label} conformance evaluation of {repo} comes back clean."
        ),
        constraints=(
            "Fix the cause. To accept it instead, record an exemption or a "
            "deferral with its reason in the repo's evaluator.yaml; never "
            "silence the check."
        ),
        sections=(*sections, ("Rule", violation_id), ("Suggestion", suggestion or "")),
        evidence=evidence,
    )


def rule(settings: Settings) -> Rule:
    """A conformance finding escalates on first sight; there is no remedy."""
    return Rule(escalate_after=1)


async def _open_keys(session: AsyncSession, prefix: str) -> list[tuple[str, str]]:
    rows = await session.execute(
        text(
            """
            SELECT kind, key FROM signal_streaks
            WHERE closed_at IS NULL AND kind = ANY(:kinds)
              AND left(key, :n) = :prefix
            ORDER BY opened_at
            """
        ),
        {"kinds": list(_KINDS), "n": len(prefix), "prefix": prefix},
    )
    return [(str(kind), str(key)) for kind, key in rows]


async def observe_row(
    session: AsyncSession,
    *,
    repo: str,
    source: str | None,
    severity: str,
    violation_id: str | None,
    finding: str | None,
    suggestion: str | None,
    settings: Settings,
    versions: Versions | None = None,
) -> list[Outcome]:
    """Hand one stored evaluation row to the signal engine.

    A failing finding is one signal; a clean evaluation recovers every open
    streak for its repo and source. Anything else says nothing.
    """
    if source not in SOURCES:
        return []
    if severity == "SUCCESS":
        label = SOURCES[source]
        evaluated = (versions or Versions()).line(repo)
        details = f"A complete {label} conformance evaluation of {repo} found nothing."
        if evaluated:
            details += f"\nEvaluated: {evaluated}"
        outcomes: list[Outcome] = []
        for kind, key in await _open_keys(session, f"{repo}/{source}/"):
            recovered = Signal(
                kind=kind,
                key=key,
                state="recovered",
                title=f"{repo}: the {label} conformance evaluation came back clean",
                details=details,
            )
            outcomes.append(
                await signals.observe(
                    session, recovered, rule=rule(settings), settings=settings
                )
            )
        return outcomes
    signal = failing_signal(
        repo=repo,
        source=source,
        severity=severity,
        violation_id=violation_id,
        finding=finding,
        suggestion=suggestion,
        versions=versions,
    )
    if signal is None:
        return []
    return [
        await signals.observe(session, signal, rule=rule(settings), settings=settings)
    ]
