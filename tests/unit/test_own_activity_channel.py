"""deejay-cog's data changes in a channel of their own, falling back to
activity while that channel's webhook is unset."""

from __future__ import annotations

import pytest

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import activity, discord

ACTIVITY_URL = "https://discord.test/api/webhooks/3/activity"
DEEJAY_COG_URL = "https://discord.test/api/webhooks/6/deejay-cog-activity"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_ACTIVITY", ACTIVITY_URL)
    monkeypatch.delenv("DEEJAY_COG_DISCORD_WEBHOOK_URL_ACTIVITY", raising=False)
    get_settings.cache_clear()
    yield get_settings
    get_settings.cache_clear()


@pytest.mark.parametrize("machine", [True, False])
def test_deejay_cog_changes_go_to_its_own_channel(machine: bool) -> None:
    assert (
        activity.change_channel("deejay-cog", machine)
        == discord.CHANNEL_DEEJAY_COG_ACTIVITY
    )


@pytest.mark.parametrize(
    "actor", ["evaluator-cog", "Kaiano Levine", "deejay-cog-extra", ""]
)
def test_other_callers_stay_in_activity(actor: str) -> None:
    assert activity.change_channel(actor, True) == discord.CHANNEL_ACTIVITY


def test_the_feed_config_routes_by_caller(settings) -> None:
    config = activity._config(settings())
    assert config.change_channel is activity.change_channel


def test_own_channel_falls_back_to_activity_while_unset(settings, monkeypatch) -> None:
    channel = discord.CHANNEL_DEEJAY_COG_ACTIVITY
    assert discord.discord_base_url(settings(), channel) == ACTIVITY_URL
    monkeypatch.setenv("DEEJAY_COG_DISCORD_WEBHOOK_URL_ACTIVITY", "   ")
    settings.cache_clear()
    assert discord.discord_base_url(settings(), channel) == ACTIVITY_URL


def test_own_channel_uses_its_webhook_once_set(settings, monkeypatch) -> None:
    monkeypatch.setenv("DEEJAY_COG_DISCORD_WEBHOOK_URL_ACTIVITY", DEEJAY_COG_URL)
    settings.cache_clear()
    assert (
        discord.discord_base_url(settings(), discord.CHANNEL_DEEJAY_COG_ACTIVITY)
        == DEEJAY_COG_URL
    )
    # Activity itself is unchanged.
    assert (
        discord.discord_base_url(settings(), discord.CHANNEL_ACTIVITY) == ACTIVITY_URL
    )
