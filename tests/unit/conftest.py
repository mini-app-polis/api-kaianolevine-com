"""Unit fixtures: nothing provisioned — no database, no network, no app client.

Settings is validated when first read and requires DATABASE_URL, so an
inert placeholder is set for the tests that read configuration; nothing in
this layer opens a connection. The environment is pinned for the same
reason the integration conftest pins it: Discord titles and the metrics
gate read it, and the launching shell's value must not leak in.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault(
    "DISCORD_WEBHOOK_URL", "https://discord.test/api/webhooks/1/token"
)
os.environ["ENVIRONMENT"] = "production"
os.environ["CLOUDWATCH_METRICS_ENABLED"] = "false"

from kaianolevine_api.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
