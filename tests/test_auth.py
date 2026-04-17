"""Project Keystone dual-auth bridge (Clerk JWT + legacy X-Owner-Id)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from kaianolevine_api import auth as auth_mod
from kaianolevine_api.auth import get_current_owner, verify_clerk_jwt


class _SettingsShim:
    """Minimal settings object for unit-testing ``get_current_owner``."""

    KAIANO_API_OWNER_ID = ""
    CLERK_JWKS_URL = "https://example.clerk.accounts.dev/.well-known/jwks.json"
    CLERK_ISSUER = "https://example.clerk.accounts.dev"


@pytest.mark.asyncio
async def test_jwt_path_returns_sub_when_clerk_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_is_enabled(name: str, session: object) -> bool:
        del session
        if name == "flags.keystone.clerk_auth_enabled":
            return True
        if name == "flags.keystone.legacy_auth_enabled":
            return False
        return True

    async def fake_verify(token: str, settings: object) -> str | None:
        del settings
        return "jwt-sub" if token == "good" else None

    monkeypatch.setattr(auth_mod, "is_enabled", fake_is_enabled)
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", fake_verify)

    owner = await get_current_owner(
        authorization="Bearer good",
        x_owner_id="legacy-owner",
        settings=_SettingsShim(),  # type: ignore[arg-type]
        session=MagicMock(),
    )
    assert owner == "jwt-sub"


@pytest.mark.asyncio
async def test_legacy_path_when_clerk_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    verify_mock = AsyncMock(side_effect=AssertionError("verify must not run"))

    async def fake_is_enabled(name: str, session: object) -> bool:
        del session
        if name == "flags.keystone.clerk_auth_enabled":
            return False
        if name == "flags.keystone.legacy_auth_enabled":
            return True
        return True

    monkeypatch.setattr(auth_mod, "is_enabled", fake_is_enabled)
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", verify_mock)

    owner = await get_current_owner(
        authorization="Bearer ignored",
        x_owner_id="header-owner",
        settings=_SettingsShim(),  # type: ignore[arg-type]
        session=MagicMock(),
    )
    assert owner == "header-owner"
    verify_mock.assert_not_called()


@pytest.mark.asyncio
async def test_both_enabled_jwt_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_is_enabled(name: str, session: object) -> bool:
        del session
        if name == "flags.keystone.clerk_auth_enabled":
            return True
        if name == "flags.keystone.legacy_auth_enabled":
            return True
        return True

    async def fake_verify(token: str, settings: object) -> str | None:
        del settings
        return "from-jwt" if token == "tok" else None

    monkeypatch.setattr(auth_mod, "is_enabled", fake_is_enabled)
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", fake_verify)

    owner = await get_current_owner(
        authorization="Bearer tok",
        x_owner_id="from-header",
        settings=_SettingsShim(),  # type: ignore[arg-type]
        session=MagicMock(),
    )
    assert owner == "from-jwt"


@pytest.mark.asyncio
async def test_both_enabled_invalid_bearer_no_legacy_fallback_raises_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoLegacy(_SettingsShim):
        KAIANO_API_OWNER_ID = ""

    async def fake_is_enabled(name: str, session: object) -> bool:
        del session
        if name == "flags.keystone.clerk_auth_enabled":
            return True
        if name == "flags.keystone.legacy_auth_enabled":
            return True
        return True

    async def fake_verify(_token: str, _settings: object) -> None:
        return None

    monkeypatch.setattr(auth_mod, "is_enabled", fake_is_enabled)
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", fake_verify)

    with pytest.raises(HTTPException) as excinfo:
        await get_current_owner(
            authorization="Bearer bad",
            x_owner_id=None,
            settings=_NoLegacy(),  # type: ignore[arg-type]
            session=MagicMock(),
        )
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_bearer_legacy_disabled_raises_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_is_enabled(name: str, session: object) -> bool:
        del session
        if name == "flags.keystone.clerk_auth_enabled":
            return True
        if name == "flags.keystone.legacy_auth_enabled":
            return False
        return True

    async def fake_verify(_token: str, _settings: object) -> None:
        return None

    monkeypatch.setattr(auth_mod, "is_enabled", fake_is_enabled)
    monkeypatch.setattr(auth_mod, "verify_clerk_jwt", fake_verify)

    with pytest.raises(HTTPException) as excinfo:
        await get_current_owner(
            authorization="Bearer bad",
            x_owner_id="would-ignored",
            settings=_SettingsShim(),  # type: ignore[arg-type]
            session=MagicMock(),
        )
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_clerk_jwt_returns_none_without_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Empty:
        CLERK_JWKS_URL = None
        CLERK_ISSUER = None

    fetch = AsyncMock(side_effect=AssertionError("JWKS must not be fetched"))
    monkeypatch.setattr(auth_mod, "_fetch_jwks_document", fetch)

    sub = await verify_clerk_jwt("any", _Empty())  # type: ignore[arg-type]
    assert sub is None
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_flags_list_still_ok_with_default_keystone_flags(client) -> None:
    """Integration: seeded DB flags keep legacy auth; default test client headers work."""
    r = await client.get("/v1/flags")
    assert r.status_code == 200
    names = {f["name"] for f in r.json()["data"]}
    assert "flags.keystone.legacy_auth_enabled" in names
    assert "flags.keystone.clerk_auth_enabled" in names
