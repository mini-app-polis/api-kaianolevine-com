"""What a send came to: the shared transport's outcome, as this service's.

Sent is sent; held is kept; failed is unconfirmed (kept, and retried a
bounded number of times); refused is not retried. A held send is waited out
within the send's wait and is not reported to Sentry, since it is kept.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import Response
from mini_app_polis import _sentry
from mini_app_polis import discord as transport
from mini_app_polis.discord import OutcomeKind, SendOutcome

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import discord

URL = "https://discord.test/api/webhooks/1/token"


@pytest.mark.parametrize(
    ("kind", "expected", "reason"),
    [
        (OutcomeKind.SENT, discord.Outcome.SENT, "forwarded"),
        (OutcomeKind.HELD, discord.Outcome.HELD, "queued"),
        (OutcomeKind.FAILED, discord.Outcome.UNCONFIRMED, "queued"),
        (OutcomeKind.REFUSED, discord.Outcome.REFUSED, "delivery_failed"),
        (OutcomeKind.NO_WEBHOOK, discord.Outcome.REFUSED, "delivery_failed"),
    ],
)
async def test_each_transport_outcome_maps_to_one_of_ours(
    monkeypatch: pytest.MonkeyPatch,
    kind: OutcomeKind,
    expected: discord.Outcome,
    reason: str,
) -> None:
    calls: list[dict[str, Any]] = []

    async def post_webhook_outcome(url: str, **kwargs: Any) -> SendOutcome:
        calls.append(kwargs)
        return SendOutcome(kind)

    monkeypatch.setattr(transport, "post_webhook_outcome", post_webhook_outcome)

    for outcome in (
        await discord.attempt_message(
            settings=get_settings(), payload={"content": "x"}
        ),
        await discord.attempt_github(
            settings=get_settings(), raw_body=b"{}", event="push"
        ),
    ):
        assert outcome is expected
        assert outcome.reason == reason
    assert [(c["try_only"], c["report_drop"]) for c in calls] == [(False, False)] * 2


@respx.mock
async def test_a_held_send_is_not_reported_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        _sentry,
        "capture_message",
        lambda message, level=None, **_: reported.append((message, level)),
    )
    respx.post(URL).mock(
        return_value=Response(429, json={"retry_after": 60, "global": False})
    )

    outcome = await discord.attempt_message(
        settings=get_settings(), payload={"content": "x"}
    )

    assert outcome is discord.Outcome.HELD
    assert [r for r in reported if r[1] == "error"] == []


@respx.mock
async def test_a_short_hold_is_waited_out(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def sleep(secs: float) -> None:
        slept.append(secs)

    monkeypatch.setattr(transport, "_sleep", sleep)
    route = respx.post(URL).mock(
        side_effect=[
            Response(429, json={"retry_after": 0.2, "global": False}),
            Response(204),
        ]
    )

    outcome = await discord.attempt_message(
        settings=get_settings(), payload={"content": "x"}, max_wait=1.0
    )

    assert outcome is discord.Outcome.SENT
    assert route.call_count == 2
    assert slept
