"""Tests for WCS source visibility service."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.models import (
    WcsSource,
    WcsSourceGrant,
    WcsTranscript,
    WcsUserProfile,
)
from kaianolevine_api.services.wcs_source_visibility import (
    user_can_see_source,
    visible_source_ids_for_user,
)


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        yield session


async def _seed_profiles(session: AsyncSession) -> None:
    session.add_all(
        [
            WcsUserProfile(user_id="admin-user", is_admin=True),
            WcsUserProfile(user_id="viewer", is_admin=False),
        ]
    )
    await session.commit()


async def _transcript(session: AsyncSession) -> uuid.UUID:
    t = WcsTranscript(
        owner_id="admin-user",
        raw_text="x",
        source_type="plaud",
        source_filename="f.txt",
        drive_file_id="d",
    )
    session.add(t)
    await session.flush()
    return t.id


async def test_default_visible_source(db_session: AsyncSession) -> None:
    await _seed_profiles(db_session)
    tid = await _transcript(db_session)
    source = WcsSource(
        owner_id="admin-user",
        transcript_id=tid,
        is_default_visible=True,
    )
    db_session.add(source)
    await db_session.commit()

    assert await user_can_see_source(db_session, "viewer", source) is True


async def test_grant_allows_viewer(db_session: AsyncSession) -> None:
    await _seed_profiles(db_session)
    tid = await _transcript(db_session)
    source = WcsSource(owner_id="admin-user", transcript_id=tid)
    db_session.add(source)
    await db_session.flush()
    db_session.add(
        WcsSourceGrant(user_id="viewer", source_id=source.id, granted_by="admin-user")
    )
    await db_session.commit()

    assert await user_can_see_source(db_session, "viewer", source) is True


async def test_private_source_denied_without_grant(db_session: AsyncSession) -> None:
    await _seed_profiles(db_session)
    tid = await _transcript(db_session)
    source = WcsSource(owner_id="admin-user", transcript_id=tid)
    db_session.add(source)
    await db_session.commit()

    assert await user_can_see_source(db_session, "viewer", source) is False


async def test_admin_sees_all_visible_ids(db_session: AsyncSession) -> None:
    await _seed_profiles(db_session)
    t1 = await _transcript(db_session)
    t2 = await _transcript(db_session)
    s1 = WcsSource(owner_id="admin-user", transcript_id=t1)
    s2 = WcsSource(owner_id="other", transcript_id=t2, is_default_visible=True)
    db_session.add_all([s1, s2])
    await db_session.commit()

    ids = await visible_source_ids_for_user(db_session, "admin-user")
    assert s1.id in ids
    assert s2.id in ids


async def test_viewer_visible_ids_default_and_grants(db_session: AsyncSession) -> None:
    await _seed_profiles(db_session)
    tv = await _transcript(db_session)
    tp = await _transcript(db_session)
    tg = await _transcript(db_session)
    visible = WcsSource(
        owner_id="other",
        transcript_id=tv,
        is_default_visible=True,
    )
    private = WcsSource(owner_id="other", transcript_id=tp)
    granted = WcsSource(owner_id="other", transcript_id=tg)
    db_session.add_all([visible, private, granted])
    await db_session.flush()
    db_session.add(
        WcsSourceGrant(user_id="viewer", source_id=granted.id, granted_by="admin")
    )
    await db_session.commit()

    ids = await visible_source_ids_for_user(db_session, "viewer")
    assert visible.id in ids
    assert granted.id in ids
    assert private.id not in ids
