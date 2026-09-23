from __future__ import annotations

import httpx
import pytest
import respx
from httpx import AsyncClient, Response

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_JSON_BODY = {
    "type": "contact",
    "originSite": "kaianolevine.com",
    "email": "sender@example.com",
    "turnstileToken": "valid-token",
    "name": "Test User",
    "message": "Hello there",
}

VALID_FORM_BODY = {
    "type": "contact",
    "originSite": "kaianolevine.com",
    "email": "sender@example.com",
    "turnstileToken": "valid-token",
    "name": "Test User",
    "message": "Hello there",
}


def _turnstile_ok(*args, **kwargs):  # noqa: ANN001
    return True


def _turnstile_fail(*args, **kwargs):  # noqa: ANN001
    return False


async def _brevo_ok(**kwargs):  # noqa: ANN001
    return True, None


async def _brevo_fail(**kwargs):  # noqa: ANN001
    return False, "Brevo error detail"


# ---------------------------------------------------------------------------
# Origin allow-list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contact_blocked_origin(client: AsyncClient) -> None:
    """Requests from disallowed origins are rejected with 403."""
    resp = await client.post(
        "/v1/contact",
        json=VALID_JSON_BODY,
        headers={"origin": "https://evil.example.com"},
    )

    # The conftest sets CONTACT_ALLOWED_ORIGINS=["https://kaianolevine.com"]
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@respx.mock
@pytest.mark.asyncio
async def test_contact_allowed_origin(client: AsyncClient) -> None:
    """Requests from an allowed origin proceed past the origin check."""
    respx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify").mock(
        return_value=Response(200, json={"success": True})
    )
    respx.post("https://api.brevo.com/v3/smtp/email").mock(
        return_value=Response(201, json={"messageId": "ok"})
    )
    resp = await client.post(
        "/v1/contact",
        json=VALID_JSON_BODY,
        headers={"origin": "https://kaianolevine.com"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Honeypot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contact_honeypot_silent_ok(client: AsyncClient) -> None:
    """Filled honeypot field returns 200 silently without sending email."""
    body = {**VALID_JSON_BODY, "website": "http://spam.example.com"}
    resp = await client.post(
        "/v1/contact",
        json=body,
        headers={"origin": "https://kaianolevine.com"},
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field", ["type", "originSite", "email", "turnstileToken"]
)
async def test_contact_missing_required_field(
    client: AsyncClient, missing_field: str
) -> None:
    body = {k: v for k, v in VALID_JSON_BODY.items() if k != missing_field}
    resp = await client.post(
        "/v1/contact",
        json=body,
        headers={"origin": "https://kaianolevine.com"},
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["details"]["missing"] == [missing_field]


# ---------------------------------------------------------------------------
# Turnstile
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_contact_turnstile_failure(client: AsyncClient) -> None:
    respx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify").mock(
        return_value=Response(200, json={"success": False})
    )
    resp = await client.post(
        "/v1/contact",
        json=VALID_JSON_BODY,
        headers={"origin": "https://kaianolevine.com"},
    )

    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "turnstile_failed"
    assert (
        err["message"] == "CAPTCHA verification failed — please refresh and try again"
    )


# ---------------------------------------------------------------------------
# Brevo
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_contact_brevo_failure(client: AsyncClient) -> None:
    respx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify").mock(
        return_value=Response(200, json={"success": True})
    )
    respx.post("https://api.brevo.com/v3/smtp/email").mock(
        return_value=Response(500, text="upstream error")
    )
    resp = await client.post(
        "/v1/contact",
        json=VALID_JSON_BODY,
        headers={"origin": "https://kaianolevine.com"},
    )

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["code"] == "email_failed"
    # The upstream body stays in the logs. Brevo's errors carry this
    # service's egress IP and a link to its admin console, and this
    # endpoint is public and unauthenticated by design.
    assert body["error"]["details"] is None
    assert "upstream error" not in resp.text


@respx.mock
@pytest.mark.asyncio
async def test_contact_turnstile_unreachable(client: AsyncClient) -> None:
    """An unreachable siteverify is this service's fault, not a failed challenge."""
    respx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify").mock(
        side_effect=httpx.ConnectTimeout("siteverify unreachable")
    )
    brevo = respx.post("https://api.brevo.com/v3/smtp/email").mock(
        return_value=Response(201, json={"messageId": "never-sent"})
    )
    resp = await client.post(
        "/v1/contact",
        json=VALID_JSON_BODY,
        headers={"origin": "https://kaianolevine.com"},
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"
    assert not brevo.called


@respx.mock
@pytest.mark.asyncio
async def test_contact_turnstile_secret_missing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing secret is a config error, not the visitor's failed CAPTCHA."""
    from kaianolevine_api.config import get_settings

    monkeypatch.setattr(get_settings(), "TURNSTILE_SECRET_KEY", None)
    siteverify = respx.post(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    ).mock(return_value=Response(200, json={"success": False}))
    resp = await client.post(
        "/v1/contact",
        json=VALID_JSON_BODY,
        headers={"origin": "https://kaianolevine.com"},
    )

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "config_error"
    assert not siteverify.called


# ---------------------------------------------------------------------------
# Form data
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_contact_form_data(client: AsyncClient) -> None:
    """Endpoint accepts application/x-www-form-urlencoded in addition to JSON."""
    respx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify").mock(
        return_value=Response(200, json={"success": True})
    )
    respx.post("https://api.brevo.com/v3/smtp/email").mock(
        return_value=Response(201, json={"messageId": "ok"})
    )
    resp = await client.post(
        "/v1/contact",
        data=VALID_FORM_BODY,
        headers={"origin": "https://kaianolevine.com"},
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Redirect
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_contact_redirect_true(client: AsyncClient) -> None:
    """redirect=true returns a 303 to {origin}/thanks/."""
    body = {**VALID_JSON_BODY, "redirect": True}
    respx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify").mock(
        return_value=Response(200, json={"success": True})
    )
    respx.post("https://api.brevo.com/v3/smtp/email").mock(
        return_value=Response(201, json={"messageId": "ok"})
    )
    resp = await client.post(
        "/v1/contact",
        json=body,
        headers={"origin": "https://kaianolevine.com"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "https://kaianolevine.com/thanks/"


@respx.mock
@pytest.mark.asyncio
async def test_contact_redirect_false(client: AsyncClient) -> None:
    """redirect=false returns plain 200 JSON."""
    body = {**VALID_JSON_BODY, "redirect": False}
    respx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify").mock(
        return_value=Response(200, json={"success": True})
    )
    respx.post("https://api.brevo.com/v3/smtp/email").mock(
        return_value=Response(201, json={"messageId": "ok"})
    )
    resp = await client.post(
        "/v1/contact",
        json=body,
        headers={"origin": "https://kaianolevine.com"},
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
