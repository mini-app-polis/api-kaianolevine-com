"""Tests for POST /v1/webhooks/doppler and GET /v1/secrets/changes.

Doppler's config.secrets.update webhook lists the secret names that changed
in a config, never values. The route verifies the delivery's signature,
stores each name once per delivery, and the read route serves them to the
rotator's sweep.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import AsyncClient
from identity.store.models import Principal, PrincipalRole
from identity.types import VerifiedSubject
from mini_app_polis.api.contract import SecretChange
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import auth as auth_mod
from kaianolevine_api.main import app

pytestmark = pytest.mark.asyncio

SECRET = "test-doppler-secret"
PROJECT = "mini-app-polis-secrets"


def _delivery(
    *,
    config: str = "dev",
    project: str = PROJECT,
    added: list[str] | None = None,
    removed: list[str] | None = None,
    updated: list[str] | None = None,
    type_: str = "config.secrets.update",
    fetched: str = "2026-10-26T22:00:00.000Z",
) -> dict[str, Any]:
    """Doppler's default payload, with the fields the route reads."""
    return {
        "type": type_,
        "config": {
            "name": config,
            "root": True,
            "environment": config,
            "project": project,
            "slug": config,
            "last_fetch_at": fetched,
        },
        "project": {"id": project, "slug": project, "name": project},
        "workplace": {"id": "w", "name": "MiniAppPolis"},
        "diff": {
            "added": added or [],
            "removed": removed or [],
            "updated": updated if updated is not None else ["OPENAI_API_KEY"],
        },
    }


def _signed(payload: dict, secret: str = SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Doppler-Signature": f"sha256={digest}",
    }


async def _post(client: AsyncClient, payload: dict, secret: str = SECRET):
    body, headers = _signed(payload, secret)
    return await client.post("/v1/webhooks/doppler", content=body, headers=headers)


async def _stored(db_session: AsyncSession) -> list[tuple[str, str, str]]:
    rows = (
        await db_session.execute(
            text("SELECT config, change, name FROM secret_changes ORDER BY name")
        )
    ).all()
    return [tuple(r) for r in rows]


@pytest.fixture
async def notifier_client(
    client, db_session: AsyncSession
) -> AsyncIterator[httpx.AsyncClient]:
    """A caller holding only ``notifier``."""
    issuer = "https://clerk.kaianolevine.com"
    principal = Principal(
        kind="human", issuer=issuer, subject="notifier-only", display_name="n"
    )
    db_session.add(principal)
    await db_session.flush()
    db_session.add(
        PrincipalRole(
            principal_id=principal.id, role_name="notifier", granted_by="test"
        )
    )
    await db_session.commit()
    original = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(
        return_value=VerifiedSubject(
            issuer=issuer, subject="notifier-only", kind="human"
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": "Bearer notifier-token"},
    ) as c:
        yield c
    auth_mod.verify_bearer = original


# ── the webhook ──────────────────────────────────────────────────────────


async def test_a_delivery_stores_each_name_it_lists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await _post(
        client,
        _delivery(
            config="prd",
            added=["NEW_KEY"],
            removed=["OLD_KEY"],
            updated=["OPENAI_API_KEY"],
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"stored": 3, "reason": None}
    assert await _stored(db_session) == [
        ("prd", "added", "NEW_KEY"),
        ("prd", "removed", "OLD_KEY"),
        ("prd", "updated", "OPENAI_API_KEY"),
    ]


async def test_a_retried_delivery_is_stored_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _delivery()
    first = await _post(client, payload)
    again = await _post(client, payload)

    assert first.json()["data"]["stored"] == 1
    assert again.status_code == 200
    assert again.json()["data"] == {"stored": 0, "reason": "retried"}
    assert len(await _stored(db_session)) == 1


async def test_the_previous_secret_is_accepted_during_a_rotation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOPPLER_WEBHOOK_SECRET", "test-doppler-secret-new")
    monkeypatch.setenv("DOPPLER_WEBHOOK_SECRET_PREVIOUS", SECRET)
    for n, secret in enumerate(("test-doppler-secret-new", SECRET)):
        response = await _post(
            client, _delivery(fetched=f"2026-10-26T22:0{n}:00.000Z"), secret
        )
        assert response.status_code == 200, secret


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Doppler-Signature": "sha256=" + "0" * 64},
        {"X-Doppler-Signature": "not-a-signature"},
    ],
)
async def test_an_unsigned_or_wrongly_signed_delivery_is_refused(
    client: AsyncClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    body = json.dumps(_delivery()).encode("utf-8")
    response = await client.post(
        "/v1/webhooks/doppler",
        content=body,
        headers={"Content-Type": "application/json", **headers},
    )

    assert response.status_code == 401
    assert await _stored(db_session) == []


async def test_another_secret_is_refused(client: AsyncClient) -> None:
    response = await _post(client, _delivery(), "someone-elses-secret")
    assert response.status_code == 401


async def test_no_secret_configured_refuses_everything(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOPPLER_WEBHOOK_SECRET", "")
    monkeypatch.setenv("DOPPLER_WEBHOOK_SECRET_PREVIOUS", SECRET)
    response = await _post(client, _delivery())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "config_error"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (_delivery(type_="config.other"), "other_type"),
        (_delivery(project="mini-app-polis-pages"), "other_project"),
        (_delivery(config="dev_personal"), "other_config"),
    ],
)
async def test_a_delivery_outside_the_record_is_acknowledged_and_not_stored(
    client: AsyncClient, db_session: AsyncSession, payload: dict, reason: str
) -> None:
    response = await _post(client, payload)

    assert response.status_code == 200
    assert response.json()["data"] == {"stored": 0, "reason": reason}
    assert await _stored(db_session) == []


async def test_only_well_formed_names_are_kept(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _post(client, _delivery(updated=["GOOD_NAME", "bad name", "x" * 200, 7]))
    assert await _stored(db_session) == [("dev", "updated", "GOOD_NAME")]


# ── reading the changes ──────────────────────────────────────────────────


async def test_changes_are_read_oldest_first_and_narrowed_by_config(
    client: AsyncClient,
) -> None:
    await _post(client, _delivery(config="prd", updated=["A_KEY"]))
    await _post(client, _delivery(config="dev", updated=["B_KEY"]))

    everything = await client.get("/v1/secrets/changes")
    dev = await client.get("/v1/secrets/changes", params={"config": "dev"})

    assert everything.status_code == 200, everything.text
    names = [SecretChange.model_validate(c).name for c in everything.json()["data"]]
    assert names == ["A_KEY", "B_KEY"]
    assert [c["name"] for c in dev.json()["data"]] == ["B_KEY"]
    assert dev.json()["meta"]["total"] == 1


async def test_changes_are_bounded_by_when_they_arrived(client: AsyncClient) -> None:
    await _post(client, _delivery(updated=["A_KEY"]))
    received = (await client.get("/v1/secrets/changes")).json()["data"][0]["changed_at"]

    since = await client.get("/v1/secrets/changes", params={"since": received})
    until = await client.get("/v1/secrets/changes", params={"until": received})

    assert [c["name"] for c in since.json()["data"]] == ["A_KEY"]
    assert until.json()["data"] == []


async def test_reading_changes_needs_the_scope(notifier_client) -> None:
    response = await notifier_client.get("/v1/secrets/changes")
    assert response.status_code == 403
