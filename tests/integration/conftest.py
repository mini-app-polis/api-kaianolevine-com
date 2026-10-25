"""Integration fixtures: real Postgres, the schema the migrations build, an app client.

Tests run against Postgres, never SQLite (TEST-015): the database the
suite exercises is the one production runs, and its schema comes from
migrations/ through the real runner — not from the models — so a
migration that drifts from the models fails here instead of in deploy.

TEST_DATABASE_URL names the database. It must be local and end in _test
(TEST-009): the session fixture drops its public schema, and every test
empties its tables.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import asyncpg
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[2]

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/kaianolevine_test",
)


def _guard(url: str) -> None:
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith(
        "_test"
    ):
        raise RuntimeError(
            f"Refusing to run tests against {parsed.hostname}{parsed.path}: "
            "TEST_DATABASE_URL must be local and its database named *_test."
        )


_guard(TEST_DATABASE_URL)

# Deterministic, not setdefault: the app builds its engine from
# DATABASE_URL, and inheriting the launching shell's value is how a test run
# would reach a real database the guard above never saw.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Contact form — dummy values so Settings validates cleanly in tests.
# Individual tests mock the actual HTTP calls to Turnstile and Brevo.
os.environ.setdefault("BREVO_API_KEY", "test-brevo-key")
os.environ.setdefault("CONTACT_TO_EMAIL", "to@example.com")
os.environ.setdefault("CONTACT_FROM_EMAIL", "from@example.com")
os.environ.setdefault("TURNSTILE_SECRET_KEY", "test-turnstile-secret")
os.environ.setdefault("CORS_ORIGINS", '["https://kaianolevine.com"]')

# Discord notifications — a dummy webhook and secret so Settings validates.
# The notification tests mock the Discord call itself with respx.
os.environ.setdefault(
    "DISCORD_WEBHOOK_URL", "https://discord.test/api/webhooks/1/token"
)
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-github-secret")
# Discord embed titles stay unmarked unless a test opts into non-production.
# Unset would resolve to local via the shared environment resolver.
# Deterministic, not setdefault: Settings is constructed at import time
# here, and setdefault would defer to whatever ENVIRONMENT the launching
# shell carries — which is how three Discord-title assertions passed in
# CI and failed locally. The autouse fixture below covers per-test
# overrides; this covers import time.
os.environ["ENVIRONMENT"] = "production"
# Production, but never publishing: the request-metrics middleware reads
# its gate when the app is built, and with the production environment above
# it would start a flush thread and reach for AWS with whatever credentials
# the launching shell has.
os.environ["CLOUDWATCH_METRICS_ENABLED"] = "false"

from identity.store import (  # noqa: E402
    Issuer,
    Principal,
    PrincipalRole,
    Role,
    RoleScope,
)
from identity.types import VerifiedSubject  # noqa: E402

from kaianolevine_api import auth as auth_mod  # noqa: E402
from kaianolevine_api.config import get_settings  # noqa: E402
from kaianolevine_api.database import get_db_session  # noqa: E402
from kaianolevine_api.main import app  # noqa: E402


def _load_migration_runner() -> Any:
    """Import scripts/apply_migrations.py — the runner the deploy uses."""
    spec = importlib.util.spec_from_file_location(
        "apply_migrations", ROOT / "scripts" / "apply_migrations.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
async def async_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1),
        echo=False,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def create_tables(async_engine) -> AsyncIterator[None]:
    """Build the schema the way production's history is built: migrations/.

    The public schema is dropped first, so every run starts from nothing
    and applies every migration — the baseline is exercised on each run.
    """
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()
    runner = _load_migration_runner()
    assert await runner._run(TEST_DATABASE_URL, False, set()) == 0
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT owner_id, name, enabled, description FROM feature_flags"
        )
    finally:
        await conn.close()
    _SEEDED_FLAGS[:] = [dict(r) for r in rows]
    yield


@pytest.fixture(autouse=True)
async def stop_auto_refresh() -> AsyncIterator[None]:
    """No automatic embeddings refresh outlives its test."""
    from kaianolevine_api.retrieval.wcs import auto_refresh

    yield
    await auto_refresh.stop()


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


#: The feature flags as the migrations seed them, read once the schema is built.
_SEEDED_FLAGS: list[dict[str, Any]] = []


@pytest.fixture(autouse=True)
async def reset_db(async_engine) -> AsyncIterator[None]:
    """Every test starts with empty tables, the migration ledger aside.

    Including the rows the migrations seed for the identity role
    vocabulary: tests seed what they need through seed_identity. The
    feature flags are the exception. A missing flag reads as off, so each
    test starts with the flags exactly as the migrations seed them, as a
    deployed database has them; a test that needs one changed updates it.
    """
    async with async_engine.begin() as conn:
        tables = (
            (
                await conn.exec_driver_sql(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'schema_migrations'"
                )
            )
            .scalars()
            .all()
        )
        if tables:
            await conn.exec_driver_sql(
                "TRUNCATE "
                + ", ".join(f'"{t}"' for t in tables)
                + " RESTART IDENTITY CASCADE"
            )
        if _SEEDED_FLAGS:
            await conn.execute(
                text(
                    "INSERT INTO feature_flags (id, owner_id, name, enabled, "
                    "description) VALUES (gen_random_uuid(), :owner_id, :name, "
                    ":enabled, :description)"
                ),
                _SEEDED_FLAGS,
            )
    yield


@pytest.fixture
async def client(async_engine) -> AsyncIterator[httpx.AsyncClient]:
    sessionmaker = async_sessionmaker(
        async_engine, expire_on_commit=False, autoflush=False
    )

    async def override_get_db_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    # Stub step 1 of the contract only. Verification is identity's
    # responsibility and is tested there; these tests are about this
    # service's adapters and its routers.
    original_verify = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(
        return_value=VerifiedSubject(
            issuer="https://clerk.kaianolevine.com",
            subject="dev-owner",
            kind="human",
        )
    )

    async with sessionmaker() as seed_session:
        await seed_identity(seed_session)

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": "Bearer test-token"},
    ) as client:
        yield client

    app.dependency_overrides.pop(get_db_session, None)
    auth_mod.verify_bearer = original_verify


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    """A bare session for tests that seed or inspect tables directly."""
    maker = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with maker() as session:
        yield session


# The default test caller. Router tests exercise endpoints, not the principal
# store, so the caller they authenticate as needs to already exist and hold
# the scopes those endpoints require — the same state a real caller reaches by
# registering once. Tests that care about an *unknown* caller use a different
# subject and are unaffected.
DEV_ISSUER = "https://clerk.kaianolevine.com"
DEV_SUBJECT = "dev-owner"

_DEV_ROLES = {
    "wcs-admin": [
        "wcs.notes.read",
        "wcs.notes.write",
        "wcs.grants.write",
        "wcs.sources.write",
        "wcs.transcripts.write",
        "wcs.embeddings.write",
        "config.flags.write",
        "wcs.corpus.read",
        "wcs.wiki.operate",
        "wcs.notes.author",
        "tasks.sweeps.create",
        "secrets.rotation-events.read",
        "secrets.rotation-events.write",
        "secrets.rotations.create",
        "secrets.key-use.read",
    ],
    "wcs-reader": ["wcs.notes.read"],
    "corpus-reader": ["wcs.notes.read", "wcs.corpus.read"],
    "wiki-writer": ["wcs.wiki.write"],
    "pipeline-writer": ["pipeline.evaluations.write", "pipeline.findings.write"],
    "catalog-ingest": [
        "catalog.sets.write",
        "catalog.tracks.write",
        "catalog.plays.write",
    ],
    "wcs-writer": [
        "wcs.notes.read",
        "wcs.notes.write",
        "wcs.sources.write",
        "wcs.transcripts.write",
    ],
    "notifier": ["notify.messages.send"],
    "standards-publisher": ["standards.catalog.publish"],
    "evaluation-trigger": ["evaluations.runs.create"],
    "wiki-operator": ["wcs.wiki.operate"],
    "deejay-trigger": ["deejay.runs.create"],
    "transcription-trigger": ["transcription.runs.create"],
    "task-requester": ["tasks.requests.create"],
    "task-groomer": ["tasks.groomings.read", "tasks.groomings.write"],
    "agent-dispatcher": ["tasks.agent-runs.read", "tasks.agent-runs.write"],
    "agent-reporter": ["tasks.agent-runs.report"],
    "secrets-rotator": [
        "secrets.rotation-events.read",
        "secrets.rotation-events.write",
        "secrets.key-use.read",
    ],
}


async def seed_identity(session: AsyncSession) -> None:
    """Idempotently create the issuer, role vocabulary and default principal."""
    from sqlalchemy import select

    if (
        await session.execute(select(Issuer).where(Issuer.issuer == DEV_ISSUER))
    ).scalars().first() is None:
        session.add(
            Issuer(
                issuer=DEV_ISSUER,
                jwks_url=f"{DEV_ISSUER}/.well-known/jwks.json",
            )
        )
    # Machines authenticate with named keys, not through an issuer with a JWKS.
    if (
        await session.execute(select(Issuer).where(Issuer.issuer == "apikey"))
    ).scalars().first() is None:
        session.add(Issuer(issuer="apikey", jwks_url=None))
    for name, scopes in _DEV_ROLES.items():
        if (
            await session.execute(select(Role).where(Role.name == name))
        ).scalars().first() is None:
            session.add(Role(name=name, description=name))
            for scope in scopes:
                session.add(RoleScope(role_name=name, scope=scope))
    await session.flush()

    principal = (
        (
            await session.execute(
                select(Principal).where(
                    Principal.issuer == DEV_ISSUER, Principal.subject == DEV_SUBJECT
                )
            )
        )
        .scalars()
        .first()
    )
    if principal is None:
        principal = Principal(
            kind="human",
            issuer=DEV_ISSUER,
            subject=DEV_SUBJECT,
            display_name="dev-owner",
        )
        session.add(principal)
        await session.flush()
        for name in _DEV_ROLES:
            session.add(
                PrincipalRole(
                    principal_id=principal.id, role_name=name, granted_by="conftest"
                )
            )
    await session.commit()


async def grant_role(async_engine, subject: str, role: str) -> None:
    """Give ``subject`` a human principal holding ``role``. Idempotent.

    Roles are the authority for access, including which notes, sources and
    wiki pages a viewer may see; the profile's is_admin flag decides nothing.
    Tests that need a viewer with the whole corpus grant them wcs-admin.
    """
    from sqlalchemy import select

    maker = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with maker() as session:
        await seed_identity(session)
        principal = (
            (
                await session.execute(
                    select(Principal).where(
                        Principal.issuer == DEV_ISSUER, Principal.subject == subject
                    )
                )
            )
            .scalars()
            .first()
        )
        if principal is None:
            principal = Principal(
                kind="human", issuer=DEV_ISSUER, subject=subject, display_name=subject
            )
            session.add(principal)
            await session.flush()
        held = (
            (
                await session.execute(
                    select(PrincipalRole).where(
                        PrincipalRole.principal_id == principal.id,
                        PrincipalRole.role_name == role,
                    )
                )
            )
            .scalars()
            .first()
        )
        if held is None:
            session.add(
                PrincipalRole(
                    principal_id=principal.id, role_name=role, granted_by="conftest"
                )
            )
        await session.commit()


@pytest.fixture(autouse=True)
def _production_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the environment so assertions do not depend on the host shell.

    Effect gates and the Discord title prefix both resolve from the
    environment, and an unset one resolves to local. Left to inherit
    whatever ENVIRONMENT the launching shell carries, this suite asserts
    different rendered titles on a laptop than in CI — and the lenient
    run is the one that hides the regression. Tests that want the
    non-production path set ENVIRONMENT themselves.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)


# A caller holding only one role: for tests of who may call what.
@pytest.fixture
def role_client(
    client, db_session: AsyncSession
) -> Callable[[str], AsyncIterator[httpx.AsyncClient]]:
    """A caller holding only the one role it is given."""

    @asynccontextmanager
    async def make(role: str) -> AsyncIterator[httpx.AsyncClient]:
        subject = f"only-{role}"
        principal = Principal(
            kind="human", issuer=DEV_ISSUER, subject=subject, display_name=subject
        )
        db_session.add(principal)
        await db_session.flush()
        db_session.add(
            PrincipalRole(principal_id=principal.id, role_name=role, granted_by="test")
        )
        await db_session.commit()
        original = auth_mod.verify_bearer
        auth_mod.verify_bearer = AsyncMock(
            return_value=VerifiedSubject(
                issuer=DEV_ISSUER, subject=subject, kind="human"
            )
        )
        try:
            async with httpx.ASGITransport(app=app) as transport:
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                    headers={"Authorization": f"Bearer {subject}"},
                ) as c:
                    yield c
        finally:
            auth_mod.verify_bearer = original

    return make
