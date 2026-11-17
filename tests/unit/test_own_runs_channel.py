"""A repo's run reports in a channel of their own (deejay-cog's), falling
back to runs while that channel's webhook is unset."""

from __future__ import annotations

import pytest

from kaianolevine_api.config import get_settings
from kaianolevine_api.routers.notifications import runs_channel
from kaianolevine_api.schemas import NotifyRequest
from kaianolevine_api.services import discord

RUNS_URL = "https://discord.test/api/webhooks/4/runs"
DEEJAY_COG_URL = "https://discord.test/api/webhooks/5/deejay-cog"


def _report(title: str) -> NotifyRequest:
    return NotifyRequest(
        embeds=[{"title": title, "footer": {"text": "SUCCESS · flow_inline · run r1"}}]
    )


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_RUNS", RUNS_URL)
    monkeypatch.delenv("DEEJAY_COG_DISCORD_WEBHOOK_URL_RUNS", raising=False)
    get_settings.cache_clear()
    yield get_settings
    get_settings.cache_clear()


def test_deejay_cog_reports_go_to_its_own_channel() -> None:
    assert (
        runs_channel(_report("deejay-cog · new_music"))
        == discord.CHANNEL_DEEJAY_COG_RUNS
    )


def test_a_development_label_does_not_hide_the_repo() -> None:
    report = _report("[DEVELOPMENT] deejay-cog · new_music")
    assert runs_channel(report) == discord.CHANNEL_DEEJAY_COG_RUNS


@pytest.mark.parametrize(
    "title", ["db-ops · db-backup", "deejay-cog", "deejay-cog-extra · flow"]
)
def test_other_reports_stay_in_runs(title: str) -> None:
    assert runs_channel(_report(title)) == discord.CHANNEL_RUNS


def test_a_plain_message_stays_in_runs() -> None:
    assert runs_channel(NotifyRequest(content="hello")) == discord.CHANNEL_RUNS


def test_own_channel_falls_back_to_runs_while_unset(settings, monkeypatch) -> None:
    assert (
        discord.discord_base_url(settings(), discord.CHANNEL_DEEJAY_COG_RUNS)
        == RUNS_URL
    )
    monkeypatch.setenv("DEEJAY_COG_DISCORD_WEBHOOK_URL_RUNS", "   ")
    settings.cache_clear()
    assert (
        discord.discord_base_url(settings(), discord.CHANNEL_DEEJAY_COG_RUNS)
        == RUNS_URL
    )


def test_own_channel_uses_its_webhook_once_set(settings, monkeypatch) -> None:
    monkeypatch.setenv(
        "DEEJAY_COG_DISCORD_WEBHOOK_URL_RUNS", f"{DEEJAY_COG_URL}/github"
    )
    settings.cache_clear()
    assert (
        discord.discord_base_url(settings(), discord.CHANNEL_DEEJAY_COG_RUNS)
        == DEEJAY_COG_URL
    )
    # Runs itself is unchanged.
    assert discord.discord_base_url(settings(), discord.CHANNEL_RUNS) == RUNS_URL
