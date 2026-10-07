"""get_db_session — the request-scoped session every router depends on.

The client fixture overrides this dependency so tests share the session-scoped
engine; this is the one place the real dependency is driven, against the test
database DATABASE_URL names (the conftest pins it).
"""

from __future__ import annotations

from sqlalchemy import text

from kaianolevine_api import database


async def test_get_db_session_yields_a_working_session_on_one_cached_maker() -> None:
    try:
        sessions = database.get_db_session()
        session = await anext(sessions)
        try:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
            assert session.bind.url.drivername == "postgresql+asyncpg"
            assert session.bind.url.database.endswith("_test")
            # Rows are readable after commit without a refresh round-trip.
            assert session.sync_session.expire_on_commit is False
        finally:
            await sessions.aclose()

        # Closing the generator closes the session it handed out.
        assert not session.in_transaction()

        url = database.get_settings().DATABASE_URL
        assert database.get_sessionmaker(url) is database.get_sessionmaker(url)
        assert database.get_engine() is database._get_engine(url)
    finally:
        await database.get_engine().dispose()
        database.get_sessionmaker.cache_clear()
        database._get_engine.cache_clear()
