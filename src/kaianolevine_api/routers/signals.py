"""Signals that close by a schedule rather than an event.

``POST /v1/signals/api-faults/quiet-sweep`` closes this service's dependency
faults (502, 503, 504) that have gone ``SIGNAL_FAULT_QUIET_HOURS`` without
another: each streak closes, and its GitHub Issue is commented and closed
(services/fault_signals.py). A 500 is never closed here. The caller is
watcher-cog's ``api-fault-quiet-sweep`` job, on a schedule declared in
mini-app-polis/infra. The sweep does its work before it answers, and asking
twice closes nothing twice.

``POST /v1/webhooks/sentry`` is the Sentry internal integration's webhook. It
takes no bearer token: it is gated by the signature Sentry computes over the
raw body with the integration's client secret (``SENTRY_WEBHOOK_SECRET``),
the only credential Sentry can present. With ``SENTRY_API_TOKEN`` set, a
delivery that would open an Issue first reads the issue's latest event for its
service and environment. An ``issue`` delivery for a watched
project's production issue opens, reopens or closes that issue's GitHub
Issue (services/sentry_signals.py). Production only, as every signal is.
Everything it does not act on answers 200, so Sentry does not retry it.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from identity.types import Principal
from mini_app_polis.environment import is_production
from mini_app_polis.logger import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import Settings, get_settings
from ..database import get_db_session
from ..schemas import (
    ApiFaultSweepResult,
    Envelope,
    SentryWebhookResult,
    api_error,
    success_envelope,
)
from ..services import fault_signals, sentry_signals
from ..services.webhook_signature import verify_sha256_signature

router = APIRouter()
logger = get_logger()


@router.post(
    "/signals/api-faults/quiet-sweep",
    response_model=Envelope[ApiFaultSweepResult],
    summary="Close the API's dependency faults that have gone quiet",
    description=(
        "Closes every open 502, 503 and 504 fault streak whose route has gone "
        "SIGNAL_FAULT_QUIET_HOURS without that fault, commenting on and "
        "closing its GitHub Issue. A 500 is never closed here: its Issue "
        "closes through a fixing pull request."
    ),
)
async def sweep_quiet_api_faults(
    principal: Principal = Depends(require_scope("signals.faults.sweep")),
) -> Envelope[ApiFaultSweepResult]:
    """Run one quiet sweep and say what it closed."""
    result = await fault_signals.sweep_quiet(get_settings())
    return success_envelope(
        ApiFaultSweepResult(quiet=result.quiet, closed=result.closed),
        count=1,
        total=1,
    )


def _sentry_result(
    *, outcome: str | None = None, reason: str | None = None
) -> Envelope[SentryWebhookResult]:
    return success_envelope(
        SentryWebhookResult(outcome=outcome, reason=reason), count=1, total=1
    )


@router.post(
    "/webhooks/sentry",
    response_model=Envelope[SentryWebhookResult],
    summary="Sentry issue webhook: one GitHub Issue per Sentry issue",
    description=(
        "Receives the Sentry internal integration's webhook, verifies "
        "Sentry-Hook-Signature (HMAC-SHA256 over the raw body) and acts on "
        "issue deliveries only: created or unresolved opens or reopens the "
        "issue's GitHub Issue in the repository that owns it, resolved closes "
        "it. One Issue per Sentry short id; a repeat opens nothing. "
        "Production issues of the projects in SENTRY_PROJECT_REPOS only."
        " Intentionally public: Sentry's request signature gates it, not a bearer token."
    ),
    include_in_schema=False,
)
async def sentry_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[SentryWebhookResult]:
    """Verify one Sentry delivery and act on it if it is a watched issue."""
    raw_body = await request.body()
    secret = (settings.SENTRY_WEBHOOK_SECRET or "").strip()
    if not secret:
        # Fail closed: an unsigned delivery never opens an Issue.
        raise api_error(500, "config_error", "Sentry webhook secret is not configured")
    if not verify_sha256_signature(
        secrets=[secret],
        raw_body=raw_body,
        signature=request.headers.get("Sentry-Hook-Signature"),
        prefix="",
    ):
        raise api_error(401, "unauthorized", "Invalid webhook signature")

    if request.headers.get("Sentry-Hook-Resource") != "issue":
        return _sentry_result(reason="other_resource")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise api_error(400, "parse_error", "Body is not valid JSON") from None
    if not isinstance(payload, dict):
        raise api_error(400, "parse_error", "Body is not a JSON object")

    action = str(payload.get("action") or "")
    if action not in (sentry_signals.OPENING_ACTIONS | sentry_signals.CLOSING_ACTIONS):
        return _sentry_result(reason="other_action")
    issue = sentry_signals.read_issue(payload)
    if issue is None:
        return _sentry_result(reason="no_issue")
    if not sentry_signals.is_watched(issue, settings):
        return _sentry_result(reason="other_project")
    if not sentry_signals.is_production(issue):
        return _sentry_result(reason="not_production")
    if not is_production():
        # This service's own development deploy acts on no real repository.
        return _sentry_result(reason="observing_off")

    token = (settings.SENTRY_API_TOKEN or "").strip()
    if token and action in sentry_signals.OPENING_ACTIONS:
        try:
            issue = await sentry_signals.latest_event(issue, token=token)
        except sentry_signals.SentryLookupError as exc:
            if not settings.SENTRY_PROJECT_REPOS.get(issue.project):
                # A shared project's repository is not guessed at: the next
                # delivery for the issue tries again.
                await sentry_signals.report_lookup_dropped(
                    issue, exc, settings=settings
                )
                return _sentry_result(outcome="dropped")
            logger.warning(
                "signals: Sentry %s: %s; using %s's repository",
                issue.short_id,
                exc,
                issue.project,
            )
        if not sentry_signals.is_production(issue):
            logger.info(
                "signals: Sentry %s is %s, not production; no Issue",
                issue.short_id,
                issue.environment,
            )
            return _sentry_result(reason="not_production")

    repo = sentry_signals.repo_for(issue, settings)
    if repo is None and action in sentry_signals.OPENING_ACTIONS:
        logger.warning(
            "signals: Sentry %s in %s names no service and its project has no "
            "repository; no Issue opened",
            issue.short_id,
            issue.project,
        )
        return _sentry_result(reason="no_repository")
    try:
        outcome = await sentry_signals.observe(
            session, issue, action=action, repo=repo, settings=settings
        )
    except Exception:  # noqa: BLE001 — logged; Sentry need not retry
        logger.exception("signals: could not observe Sentry %s", issue.short_id)
        return _sentry_result(reason="error")
    logger.info("signals: Sentry %s %s -> %s", issue.short_id, action, outcome)
    return _sentry_result(outcome=outcome)
