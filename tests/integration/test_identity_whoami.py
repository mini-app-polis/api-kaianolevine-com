"""GET /v1/identity/whoami."""

from __future__ import annotations

import uuid

import pytest
from identity.store import Principal, PrincipalRole
from identity.types import VerifiedSubject

from kaianolevine_api import auth as auth_mod

ISSUER = "https://clerk.kaianolevine.com"


def _vs(subject: str, kind: str = "machine") -> VerifiedSubject:
    return VerifiedSubject(issuer=ISSUER, subject=subject, kind=kind)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_unknown_subject_reports_the_string_to_seed(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case this endpoint exists for: a Clerk machine with no principal.

    The caller is authenticated but unknown, and the response hands back the
    exact subject to seed rather than leaving it to be guessed.
    """
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        auth_mod, "verify_bearer", AsyncMock(return_value=_vs("mch_brand_new"))
    )
    r = await client.get("/v1/identity/whoami")
    assert r.status_code == 200
    body = r.json()
    assert body["verified"]["subject"] == "mch_brand_new"
    assert body["verified"]["kind"] == "machine"
    assert body["principal"] is None
    assert "Seed it" in body["hint"]


@pytest.mark.asyncio
async def test_known_subject_reports_principal_and_roles(
    client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    await db_session.flush()
    pid = uuid.uuid4()
    db_session.add(
        Principal(
            id=pid,
            kind="machine",
            issuer=ISSUER,
            subject="mch_deejay_cog",
            display_name="deejay-cog",
        )
    )
    db_session.add(PrincipalRole(principal_id=pid, role_name="catalog-ingest"))
    await db_session.commit()

    monkeypatch.setattr(
        auth_mod, "verify_bearer", AsyncMock(return_value=_vs("mch_deejay_cog"))
    )
    r = await client.get("/v1/identity/whoami")
    assert r.status_code == 200
    body = r.json()
    assert body["principal"]["id"] == str(pid)
    assert body["principal"]["roles"] == ["catalog-ingest"]
    assert body["principal"]["display_name"] == "deejay-cog"


@pytest.mark.asyncio
async def test_requires_a_credential(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unauthenticated callers learn nothing."""
    from kaianolevine_api.schemas import api_error

    async def _reject(*_a, **_k):
        raise api_error(401, "unauthorized", "Valid Bearer token required")

    monkeypatch.setattr(auth_mod, "verify_bearer", _reject)
    r = await client.get("/v1/identity/whoami")
    assert r.status_code == 401
