"""Signals that close by a schedule rather than an event.

``POST /v1/signals/api-faults/quiet-sweep`` closes this service's dependency
faults (502, 503, 504) that have gone ``SIGNAL_FAULT_QUIET_HOURS`` without
another: each streak closes, and its GitHub Issue is commented and closed
(services/fault_signals.py). A 500 is never closed here. The caller is
watcher-cog's ``api-fault-quiet-sweep`` job, on a schedule declared in
mini-app-polis/infra. The sweep does its work before it answers, and asking
twice closes nothing twice.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from identity.types import Principal

from ..auth import require_scope
from ..config import get_settings
from ..schemas import ApiFaultSweepResult, Envelope, success_envelope
from ..services import fault_signals

router = APIRouter()


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
