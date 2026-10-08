"""database.py — the engine URL every deploy's DATABASE_URL is turned into.

Hosting providers hand out ``postgres://`` and ``postgresql://`` URLs. Either
would make SQLAlchemy reach for psycopg2, which this project does not
install, so both are rewritten to the asyncpg driver. Building an engine
opens no connection, so none of this needs a database.
"""

from __future__ import annotations

import pytest

from kaianolevine_api import database
from kaianolevine_api.config import get_settings

# The undecorated builder: calling it leaves the process-wide cache alone.
_build = database._get_engine.__wrapped__


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@localhost:5432/app_test",
        "postgres://u:p@localhost:5432/app_test",
        "postgresql+asyncpg://u:p@localhost:5432/app_test",
    ],
)
def test_every_postgres_url_form_uses_the_asyncpg_driver(url: str) -> None:
    engine = _build(url)

    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.host == "localhost"
    assert engine.url.port == 5432
    assert engine.url.database == "app_test"
    assert engine.url.username == "u"


def test_engine_keeps_bound_parameters_out_of_exceptions() -> None:
    """hide_parameters stops a failed statement's values reaching a log line."""
    engine = _build("postgresql://u:p@localhost:5432/app_test")

    assert engine.sync_engine.hide_parameters is True
    assert engine.sync_engine.pool._pre_ping is True


def test_get_engine_builds_from_the_settings_database_url(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(database, "_get_engine", lambda url: seen.append(url) or url)

    settings = get_settings().model_copy(
        update={"DATABASE_URL": "postgres://localhost:5432/explicit_test"}
    )
    assert database.get_engine(settings) == "postgres://localhost:5432/explicit_test"
    # Without an argument, the process settings decide.
    assert database.get_engine() == get_settings().DATABASE_URL
    assert seen == [
        "postgres://localhost:5432/explicit_test",
        get_settings().DATABASE_URL,
    ]
