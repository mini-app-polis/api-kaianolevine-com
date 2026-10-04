"""The corpus scope keeps private sources away from ordinary readers.

Three endpoints return the WCS corpus with per-source visibility filtering
bypassed. Every human principal holds ``wcs.notes.read`` by default — it is
the only scope in the ``wcs-reader`` role, and every provisioned human gets
that role — so guarding these on ``wcs.notes.read`` grants the full corpus,
private sources included, to every signed-in user.

They require ``wcs.corpus.read`` instead, held by ``wcs-admin`` (human
administrators) and ``corpus-reader`` (wiki-curator-cog). These tests pin
that: a principal holding only ``wcs-reader`` is denied all three.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from identity.store.models import Principal, PrincipalRole
from identity.types import VerifiedSubject
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import auth as auth_mod
from kaianolevine_api.main import app

READER_SUBJECT = "plain-reader"
ISSUER = "https://clerk.kaianolevine.com"

# Bypass per-source visibility. Guarding any of these on a scope the
# default human role holds is a corpus leak.
CORPUS_ROUTES = [
    "/v1/wcs/notes/all",
    "/v1/wcs/wiki/admin/sources",
    "/v1/wcs/wiki/export",
    # The gaps endpoints enumerate the whole substrate and discard the
    # principal entirely (`_ = admin_id_principal.subject`), so there is no
    # per-caller filtering behind the scope check.
    "/v1/wcs/admin/gaps/orphan-entities",
    "/v1/wcs/admin/gaps/stub-entities",
    "/v1/wcs/admin/gaps/skills-unpaired",
    "/v1/wcs/admin/gaps/sources-uncomposed",
]


@pytest.fixture
async def reader_client(
    client, db_session: AsyncSession
) -> AsyncIterator[httpx.AsyncClient]:
    """A real, provisioned human holding only the default reader role."""
    principal = Principal(
        kind="human", issuer=ISSUER, subject=READER_SUBJECT, display_name="reader"
    )
    db_session.add(principal)
    await db_session.flush()
    db_session.add(
        PrincipalRole(
            principal_id=principal.id, role_name="wcs-reader", granted_by="test"
        )
    )
    await db_session.commit()

    original = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(
        return_value=VerifiedSubject(
            issuer=ISSUER, subject=READER_SUBJECT, kind="human"
        )
    )
    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer reader-token"},
        ) as c:
            yield c
    auth_mod.verify_bearer = original


@pytest.mark.parametrize("path", CORPUS_ROUTES)
async def test_plain_reader_denied_corpus_routes(reader_client, path: str) -> None:
    """wcs-reader is not enough for a route that bypasses visibility."""
    resp = await reader_client.get(path)
    assert resp.status_code == 403, (
        f"{path} allowed a wcs-reader. Every human holds wcs.notes.read, so this "
        f"route now returns private sources to every signed-in user."
    )


async def test_plain_reader_denied_single_source_admin_view(reader_client) -> None:
    """The per-source admin view passes bypass_visibility=True."""
    resp = await reader_client.get("/v1/wcs/wiki/admin/sources/some-source-id")
    assert resp.status_code == 403


async def test_plain_reader_still_reads_its_own_notes(reader_client) -> None:
    """The fix must not take away what wcs-reader is for."""
    resp = await reader_client.get("/v1/wcs/notes")
    assert resp.status_code != 403


@pytest.mark.parametrize("path", CORPUS_ROUTES)
async def test_corpus_holder_allowed(client, path: str) -> None:
    """dev-owner holds every role, wcs-admin and corpus-reader included."""
    resp = await client.get(path)
    assert resp.status_code != 403
