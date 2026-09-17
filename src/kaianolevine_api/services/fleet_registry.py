"""The fleet roster, and what counts as one unit of evaluation.

Two jobs that look like one. The first is fetching ``ecosystem.yaml``,
which is a cache in front of a raw GitHub URL and is as dull as it
sounds. The second is turning that file into *evaluation units*, and it
is not dull at all — a unit is a repository, not a service, and for a
monorepo those differ.

**A monorepo is one unit carrying every app in it.** Sibling
deduplication treats an identical finding on two apps as a single issue,
and it cannot know that until every app in the workspace has been
evaluated — so they have to arrive together or not at all. Group them
here and the evaluator sees the workspace; flatten them into one unit per
app and it sees several unrelated repositories that happen to share a
name, loses ``monorepo_root`` and the workspace ``package.json``, and
posts the same finding once per app. That failure looks like a clean run
from the outside, which is the shape this fleet has been bitten by most.

**Ported from evaluator-cog's ``_fleet_events``, deliberately.** Both
exist while the sweep is still the proven path, and two implementations
of the same grouping that drift apart would produce two different
answers to "what is the fleet" with nothing to say which is right. When
``run_fleet_sweep`` is retired, that one deletes and this one remains.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml
from mini_app_polis.logger import get_logger

logger = get_logger()

#: Where the registry lives.
#:
#: Raw GitHub rather than a table, and that is a deliberate down payment
#: rather than an end state. The API should be the system of record for
#: the roster — it already is for the standards catalog — and when it is,
#: only this constant and the fetch below change. Everything downstream
#: is already talking to this module instead of to GitHub.
ECOSYSTEM_YAML_URL = (
    "https://raw.githubusercontent.com/"
    "mini-app-polis/ecosystem-standards/main/ecosystem.yaml"
)

#: The fleet default when a registry entry does not name an org.
DEFAULT_ORG = "mini-app-polis"

#: The branch a registry entry evaluates on when it does not name one.
DEFAULT_BRANCH = "main"

#: How long a fetched roster is reused.
#:
#: The roster changes when a repository is added to the fleet, which is
#: rare, and this sits on the request path of a route that answers a CI
#: runner. Five minutes is long enough that a burst of releases costs one
#: fetch and short enough that nobody waits for a new repository.
CACHE_TTL_SECONDS = 300

#: How long to wait on GitHub before giving up.
#:
#: Short on purpose. A slow fetch here delays an acknowledgement that CI
#: is waiting on, and a stale roster is a better answer than a late one —
#: see the serve-stale path in :func:`fetch_ecosystem`.
FETCH_TIMEOUT_SECONDS = 10.0


class RegistryError(RuntimeError):
    """The roster could not be read, and no usable copy was held."""


@dataclass(frozen=True)
class EvaluationUnit:
    """One repository to evaluate, and everything the evaluator needs.

    The same shape ``EvaluationEvent`` has on the other side, minus the
    run id and mode, which belong to the pass rather than to the unit.
    """

    org: str
    repo: str
    ref: str
    services: tuple[dict[str, Any], ...]
    monorepo: dict[str, Any] | None = None

    @property
    def service_ids(self) -> list[str]:
        """The declared service ids, skipping any service that has none."""
        return [str(s.get("id") or "") for s in self.services if s.get("id")]


@dataclass
class _Cache:
    """A roster and when it was fetched. Module state, held on purpose.

    The alternative is a fetch per dispatch, and a fan-out triggered by a
    catalog release is exactly when several dispatches arrive together.
    """

    document: dict[str, Any] | None = None
    fetched_at: float = 0.0
    lock: Any = field(default=None, repr=False)


_cache = _Cache()


def _fresh(now: float) -> bool:
    return _cache.document is not None and (now - _cache.fetched_at) < CACHE_TTL_SECONDS


def fetch_ecosystem(*, force: bool = False) -> dict[str, Any]:
    """Return the registry document, from cache when it is fresh enough.

    Serves a stale copy when GitHub fails and one is held. That is the
    right trade for this caller: the roster changes rarely, so a copy
    minutes or hours old is almost certainly still correct, while a failed
    dispatch means a whole fleet pass silently does not happen. A roster
    that is slightly old evaluates the right repositories; no roster
    evaluates none of them.

    Raises :class:`RegistryError` only when the fetch fails and nothing was
    ever held — the first call after a deploy, where there is no older
    answer to fall back to and pretending otherwise would be inventing one.
    """
    now = time.monotonic()
    if not force and _fresh(now):
        return _cache.document  # type: ignore[return-value]

    try:
        response = httpx.get(
            ECOSYSTEM_YAML_URL,
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"Accept": "text/plain"},
            follow_redirects=True,
        )
        response.raise_for_status()
        document = yaml.safe_load(response.text)
    except Exception as exc:  # noqa: BLE001 — every failure is the same failure here
        if _cache.document is not None:
            age = int(now - _cache.fetched_at)
            logger.warning(
                "fleet registry: could not refresh ecosystem.yaml (%s) — "
                "using the copy fetched %ss ago",
                exc,
                age,
            )
            return _cache.document
        raise RegistryError(f"could not read the fleet registry: {exc}") from exc

    if not isinstance(document, dict):
        # A YAML document that parses to a string or a list is a published
        # file that is wrong, not a transport problem, so the held copy is
        # not obviously worse. Same reasoning as the branch above.
        if _cache.document is not None:
            logger.warning(
                "fleet registry: ecosystem.yaml did not parse to a mapping — "
                "using the previous copy"
            )
            return _cache.document
        raise RegistryError("the fleet registry did not parse to a mapping")

    _cache.document = document
    _cache.fetched_at = now
    return document


def active_services(ecosystem: dict[str, Any]) -> list[dict[str, Any]]:
    """Every service the registry marks active."""
    services = ecosystem.get("services") or []
    if not isinstance(services, list):
        return []
    return [s for s in services if isinstance(s, dict) and s.get("status") == "active"]


def monorepos(ecosystem: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{monorepo_id: record}``, keyed to match services' ``monorepo`` field."""
    records = ecosystem.get("monorepos") or []
    if not isinstance(records, list):
        return {}
    return {str(m["id"]): m for m in records if isinstance(m, dict) and m.get("id")}


def declared_org(record: dict[str, Any] | None) -> str:
    """The org a registry entry names, else the fleet default.

    Almost every repository omits this and lives under ``mini-app-polis``,
    but not all of them do. With the org assumed, a repository in a
    personal org 404'd on every run — registered, declaring itself
    governed, and never once evaluated.
    """
    if not isinstance(record, dict):
        return DEFAULT_ORG
    return str(record.get("org") or "").strip() or DEFAULT_ORG


def declared_branch(record: dict[str, Any] | None) -> str:
    """The branch a registry entry names, else ``main``.

    Read from the service record for a plain repository and from the
    monorepo record for a monorepo, because that is where each one names
    its repository.
    """
    if not isinstance(record, dict):
        return DEFAULT_BRANCH
    return str(record.get("branch") or "").strip() or DEFAULT_BRANCH


def evaluation_units(ecosystem: dict[str, Any]) -> list[EvaluationUnit]:
    """Turn the registry into one unit per repository.

    A service id appears at most once. Duplicate registry rows are a
    data-quality problem in their own right, and evaluating one twice
    would post two sets of findings for the same repository in the same
    run — which then reads as a deduplication failure rather than as the
    registry error it is.
    """
    records = monorepos(ecosystem)
    seen: set[str] = set()
    units: list[EvaluationUnit] = []
    grouped: dict[str, list[dict[str, Any]]] = {}

    for service in active_services(ecosystem):
        service_id = str(service.get("id") or "")
        if not service_id:
            continue
        if service_id in seen:
            logger.warning("fleet registry: skipping duplicate service %s", service_id)
            continue
        seen.add(service_id)

        monorepo_id = str(service.get("monorepo") or "")
        if monorepo_id:
            grouped.setdefault(monorepo_id, []).append(service)
            continue

        units.append(
            EvaluationUnit(
                org=declared_org(service),
                repo=str(service.get("repo") or service_id),
                ref=declared_branch(service),
                services=(service,),
            )
        )

    for monorepo_id, services in grouped.items():
        record = records.get(monorepo_id)
        if record is None:
            # Declared membership of a monorepo the registry does not
            # describe. Each app is still a repository as far as the
            # registry is concerned, so evaluate them that way rather than
            # dropping them — a repository that vanishes from a fleet pass
            # because of a typo in someone else's record is the silent
            # gap this whole path exists to prevent.
            logger.warning(
                "fleet registry: monorepo %s is not in the registry — "
                "evaluating its apps individually",
                monorepo_id,
            )
            units.extend(
                EvaluationUnit(
                    org=declared_org(service),
                    repo=str(service.get("repo") or service.get("id")),
                    ref=declared_branch(service),
                    services=(service,),
                )
                for service in services
            )
            continue

        units.append(
            EvaluationUnit(
                org=declared_org(record),
                repo=str(record.get("repo") or monorepo_id),
                ref=declared_branch(record),
                services=tuple(services),
                monorepo=record,
            )
        )

    return units


def fleet(*, force: bool = False) -> list[EvaluationUnit]:
    """The fleet as units, fetching the roster if the cache is cold."""
    return evaluation_units(fetch_ecosystem(force=force))


def reset_cache() -> None:
    """Drop the held roster. For tests, and for a future admin refresh."""
    _cache.document = None
    _cache.fetched_at = 0.0
