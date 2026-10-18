"""Tests for cog-only full-corpus wiki export."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from identity.types import VerifiedSubject
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api import auth as auth_mod
from kaianolevine_api.models import WcsEntity, WcsSource, WcsTranscript
from kaianolevine_api.services import wcs_wiki as wiki_svc
from tests.integration.test_wcs_sources_endpoint import (
    _create_transcript,
    _source_payload,
)


def _vs(subject: str, kind: str = "human"):
    """A verified credential, stubbed.

    Verification moved to the identity binding and is tested there; these
    tests only need step 1 to have produced a subject.
    """
    return VerifiedSubject(
        issuer="https://clerk.kaianolevine.com",
        subject=subject,
        kind=kind,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
async def seed_dev_owner_wcs_admin(reset_db, async_engine) -> None:
    from sqlalchemy import text

    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('dev-owner', '', '', true) "
                "ON CONFLICT (user_id) DO UPDATE SET is_admin = excluded.is_admin"
            )
        )


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        yield session


async def test_export_wiki_corpus_returns_full_database(
    db_session: AsyncSession,
) -> None:
    t1 = WcsTranscript(
        owner_id="dev-owner",
        raw_text="a",
        source_type="plaud",
        source_filename="a.txt",
        drive_file_id=f"d-{uuid.uuid4().hex[:8]}",
    )
    t2 = WcsTranscript(
        owner_id="dev-owner",
        raw_text="b",
        source_type="plaud",
        source_filename="b.txt",
        drive_file_id=f"d-{uuid.uuid4().hex[:8]}",
    )
    db_session.add_all([t1, t2])
    await db_session.flush()
    private = WcsSource(
        owner_id="dev-owner",
        transcript_id=t1.id,
        is_default_visible=False,
    )
    public = WcsSource(
        owner_id="dev-owner",
        transcript_id=t2.id,
        is_default_visible=True,
    )
    db_session.add_all([private, public])
    await db_session.commit()

    export = await wiki_svc.export_wiki_corpus(db_session)
    source_ids = {s.id for s in export.sources}
    assert private.id in source_ids
    assert public.id in source_ids

    db_source_count = (
        await db_session.execute(select(func.count()).select_from(WcsSource))
    ).scalar_one()
    db_entity_count = (
        await db_session.execute(
            select(func.count())
            .select_from(WcsEntity)
            .where(WcsEntity.merged_into_id.is_(None))
        )
    ).scalar_one()
    assert len(export.sources) == db_source_count
    assert len(export.entities) == db_entity_count


async def test_export_requires_the_corpus_scope_not_merely_read(client) -> None:
    """A reader's scope must not open the unfiltered corpus.

    The gate used to be "machine callers only", which excluded every human.
    It is now `wcs.corpus.read`, held by wiki-curator-cog and admins. Mapping
    this endpoint to `wcs.notes.read` would have handed the full corpus —
    private sources included — to every signed-in reader, so this asserts the
    narrower scope is what is actually checked.
    """
    from kaianolevine_api.routers import wcs_wiki

    src = wcs_wiki.__file__
    with open(src) as fh:
        body = fh.read()
    assert 'require_scope("wcs.corpus.read")' in body
    assert 'require_scope("wcs.notes.read")' not in body.split("wiki/export")[-1][:600]


async def test_export_forbidden_for_a_caller_without_a_principal(client) -> None:
    """A valid credential this ecosystem does not know is still refused.

    The credential verifies; resolve finds nothing; authorize denies with
    principal_not_found. Authentication and authorization failing separately
    is the point of keeping them separate.
    """
    original_verify = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(return_value=_vs("stranger-user", "human"))
    try:
        resp = await client.get("/v1/wcs/wiki/export")
    finally:
        auth_mod.verify_bearer = original_verify
    assert resp.status_code == 403
    auth_mod.verify_bearer = original_verify


async def test_corpus_scope_holder_gets_the_full_corpus(client) -> None:
    transcript_id = await _create_transcript(client)
    create = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            transcript_id,
            is_default_visible=False,
            visibility="private",
            title="Cog export lesson",
        ),
    )
    assert create.status_code == 200
    private_id = create.json()["data"]["id"]

    # The fixture caller holds wcs.corpus.read; that scope, not the caller
    # being a machine, is what opens the unfiltered corpus.
    resp = await client.get("/v1/wcs/wiki/export")

    assert resp.status_code == 200
    source_ids = {s["id"] for s in resp.json()["data"]["sources"]}
    assert private_id in source_ids


# ── Built once per revision (migration 058) ────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_export_cache() -> None:
    from kaianolevine_api.services import wcs_wiki_export as export_svc

    export_svc.cache.clear()


async def _revision(async_engine) -> int:
    from sqlalchemy import text

    async with async_engine.connect() as conn:
        value = (
            await conn.execute(text("SELECT revision FROM wcs_corpus_revision"))
        ).scalar_one_or_none()
    return value or 0


async def _add_entity(session: AsyncSession, slug: str) -> WcsEntity:
    entity = WcsEntity(slug=slug, canonical_name=slug.title(), kind="concept")
    session.add(entity)
    await session.flush()
    return entity


async def test_a_transaction_bumps_the_revision_once_at_commit(
    async_engine, db_session: AsyncSession
) -> None:
    before = await _revision(async_engine)

    await _add_entity(db_session, "anchor")
    await _add_entity(db_session, "stretch")
    # Not yet committed: nobody else sees a new revision.
    assert await _revision(async_engine) == before

    await db_session.commit()
    assert await _revision(async_engine) == before + 1


async def test_a_rolled_back_write_does_not_bump(
    async_engine, db_session: AsyncSession
) -> None:
    before = await _revision(async_engine)

    await _add_entity(db_session, "anchor")
    await db_session.rollback()

    assert await _revision(async_engine) == before


async def test_updates_deletes_and_truncates_bump(
    async_engine, db_session: AsyncSession
) -> None:
    from sqlalchemy import text

    entity = await _add_entity(db_session, "anchor")
    await db_session.commit()
    start = await _revision(async_engine)

    entity.canonical_name = "Anchor step"
    await db_session.commit()
    assert await _revision(async_engine) == start + 1

    await db_session.delete(entity)
    await db_session.commit()
    assert await _revision(async_engine) == start + 2

    async with async_engine.begin() as conn:
        await conn.execute(text("TRUNCATE wcs_entity_aliases CASCADE"))
    assert await _revision(async_engine) == start + 3


async def test_a_table_outside_the_export_does_not_bump(async_engine) -> None:
    from sqlalchemy import text

    before = await _revision(async_engine)
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_wiki_pages (slug, kind, name, document) "
                "VALUES ('anchor', 'concept', 'Anchor', '{}')"
            )
        )

    assert await _revision(async_engine) == before


async def test_a_new_deploy_changes_the_etag(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New code may emit a different body for the same data: no 304 across it."""
    from kaianolevine_api.services import wcs_wiki_export as export_svc

    first = await client.get("/v1/wcs/wiki/export")
    monkeypatch.setattr(export_svc, "BUILD", "nextdeploy")
    export_svc.cache.clear()

    after = await client.get(
        "/v1/wcs/wiki/export", headers={"If-None-Match": first.headers["etag"]}
    )

    assert after.status_code == 200
    assert after.headers["etag"] != first.headers["etag"]


async def test_an_unchanged_corpus_answers_304(client) -> None:
    first = await client.get("/v1/wcs/wiki/export")
    etag = first.headers["etag"]

    again = await client.get("/v1/wcs/wiki/export", headers={"If-None-Match": etag})

    assert first.status_code == 200
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == etag


async def test_a_write_changes_the_etag_and_the_body(client) -> None:
    first = await client.get("/v1/wcs/wiki/export")
    transcript_id = await _create_transcript(client)
    created = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(transcript_id, title="After the first read"),
    )
    assert created.status_code == 200

    second = await client.get(
        "/v1/wcs/wiki/export", headers={"If-None-Match": first.headers["etag"]}
    )

    assert second.status_code == 200
    assert second.headers["etag"] != first.headers["etag"]
    ids = {s["id"] for s in second.json()["data"]["sources"]}
    assert created.json()["data"]["id"] in ids


async def test_readers_of_one_revision_share_one_build(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = 0
    real = wiki_svc.export_wiki_corpus

    async def counting(session: AsyncSession):
        nonlocal builds
        builds += 1
        return await real(session)

    monkeypatch.setattr(wiki_svc, "export_wiki_corpus", counting)

    a = await client.get("/v1/wcs/wiki/export")
    b = await client.get("/v1/wcs/wiki/export")

    assert builds == 1
    assert a.content == b.content
    assert a.headers["etag"] == b.headers["etag"]


async def test_the_body_is_what_the_route_returned_before_the_cache(
    client, db_session: AsyncSession
) -> None:
    """Same envelope, same field names: the cog's contract is unchanged."""
    from fastapi.encoders import jsonable_encoder

    from kaianolevine_api.config import get_settings
    from kaianolevine_api.schemas import success_envelope

    transcript_id = await _create_transcript(client)
    await client.post("/v1/wcs/sources", json=_source_payload(transcript_id))

    resp = await client.get("/v1/wcs/wiki/export")
    expected = jsonable_encoder(
        success_envelope(
            await wiki_svc.export_wiki_corpus(db_session),
            count=1,
            total=1,
            version=get_settings().API_VERSION,
        )
    )

    got = resp.json()
    got["data"].pop("exported_at")
    expected["data"].pop("exported_at")
    assert got == expected


async def test_a_reader_past_the_line_gets_503_with_retry_after(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kaianolevine_api.services import wcs_wiki_export as export_svc

    busy = export_svc.ExportCache(max_waiting=0)
    monkeypatch.setattr(export_svc, "cache", busy)
    await busy._lock.acquire()
    try:
        resp = await client.get("/v1/wcs/wiki/export")
    finally:
        busy._lock.release()

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == str(export_svc.RETRY_AFTER_S)
    assert resp.json()["error"]["code"] == "export_busy"
