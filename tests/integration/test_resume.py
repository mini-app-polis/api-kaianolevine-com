from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from unittest.mock import patch
from urllib.parse import parse_qs

import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from httpx import AsyncClient, Response

from kaianolevine_api.config import get_settings


@pytest.fixture(autouse=True)
def clear_resume_token_cache() -> Iterator[None]:
    from kaianolevine_api.routers import resume as resume_mod

    resume_mod._token_cache["token"] = None
    resume_mod._token_cache["expires_at"] = 0.0
    yield
    resume_mod._token_cache["token"] = None
    resume_mod._token_cache["expires_at"] = 0.0


@pytest.mark.asyncio
async def test_resume_501_when_resume_file_id_missing(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    monkeypatch.delenv("RESUME_FILE_ID", raising=False)
    get_settings.cache_clear()
    resp = await client.get("/v1/resume")
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "not_configured"
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_resume_200_headers_and_streaming_body(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    monkeypatch.setenv("RESUME_FILE_ID", "file-abc")
    monkeypatch.setenv("GOOGLE_CLIENT_EMAIL", "svc@proj.iam.gserviceaccount.com")
    monkeypatch.setenv("GOOGLE_PRIVATE_KEY", "dummy")
    get_settings.cache_clear()

    file_id = "file-abc"
    meta_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=Response(
            200, json={"access_token": "test-token", "expires_in": 3600}
        )
    )
    respx.get(meta_url).mock(
        side_effect=[
            Response(
                200,
                json={
                    "id": "fid",
                    "name": 'Re"sume\r\n.pdf',
                    "mimeType": "application/pdf",
                    "size": "10",
                    "webViewLink": "https://example.com",
                },
            ),
            Response(
                200, content=b"%PDF-1.4", headers={"Content-Type": "application/pdf"}
            ),
        ]
    )
    with patch(
        "kaianolevine_api.routers.resume._build_service_account_jwt",
        return_value="jwt",
    ):
        resp = await client.get("/v1/resume")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.headers["cache-control"] == "public, max-age=3600"
    assert (
        resp.headers["content-security-policy"]
        == "frame-ancestors https://software.kaianolevine.com"
    )
    assert resp.headers["content-disposition"] == 'inline; filename="Resume.pdf"'
    lowered = {k.lower() for k in resp.headers.keys()}
    assert "x-frame-options" not in lowered
    assert resp.content == b"%PDF-1.4"
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_resume_502_when_drive_metadata_fails(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    monkeypatch.setenv("RESUME_FILE_ID", "file-abc")
    monkeypatch.setenv("GOOGLE_CLIENT_EMAIL", "svc@proj.iam.gserviceaccount.com")
    monkeypatch.setenv("GOOGLE_PRIVATE_KEY", "dummy")
    get_settings.cache_clear()

    file_id = "file-abc"
    meta_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=Response(
            200, json={"access_token": "test-token", "expires_in": 3600}
        )
    )
    respx.get(meta_url).mock(return_value=Response(404, json={}))
    with patch(
        "kaianolevine_api.routers.resume._build_service_account_jwt",
        return_value="jwt",
    ):
        resp = await client.get("/v1/resume")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_resume_502_when_drive_download_fails(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    monkeypatch.setenv("RESUME_FILE_ID", "file-abc")
    monkeypatch.setenv("GOOGLE_CLIENT_EMAIL", "svc@proj.iam.gserviceaccount.com")
    monkeypatch.setenv("GOOGLE_PRIVATE_KEY", "dummy")
    get_settings.cache_clear()

    file_id = "file-abc"
    meta_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=Response(
            200, json={"access_token": "test-token", "expires_in": 3600}
        )
    )
    respx.get(meta_url).mock(
        side_effect=[
            Response(
                200,
                json={
                    "id": "fid",
                    "name": 'Re"sume\r\n.pdf',
                    "mimeType": "application/pdf",
                    "size": "10",
                    "webViewLink": "https://example.com",
                },
            ),
            Response(403, json={}),
        ]
    )
    with patch(
        "kaianolevine_api.routers.resume._build_service_account_jwt",
        return_value="jwt",
    ):
        resp = await client.get("/v1/resume")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"
    get_settings.cache_clear()


# ── Service-account token exchange ────────────────────────────────────────────

_SVC_EMAIL = "svc@proj.iam.gserviceaccount.com"
_META_URL = "https://www.googleapis.com/drive/v3/files/file-abc"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _configure(monkeypatch: pytest.MonkeyPatch, private_key: str) -> None:
    monkeypatch.setenv("RESUME_FILE_ID", "file-abc")
    monkeypatch.setenv("GOOGLE_CLIENT_EMAIL", _SVC_EMAIL)
    monkeypatch.setenv("GOOGLE_PRIVATE_KEY", private_key)
    get_settings.cache_clear()


def _mock_drive() -> None:
    respx.get(_META_URL).mock(
        side_effect=lambda request: (
            Response(200, content=b"%PDF-1.4")
            if request.url.params.get("alt") == "media"
            else Response(
                200, json={"name": "resume.pdf", "mimeType": "application/pdf"}
            )
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_resume_exchanges_a_verifiable_rs256_assertion(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    """The assertion Google receives is an RS256 JWT signed by the configured key."""
    key = _rsa_key()
    # The env form carries literal "\n"; Settings turns them back into newlines.
    _configure(monkeypatch, _pem(key).replace("\n", "\\n"))
    token_route = respx.post(_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    _mock_drive()

    resp = await client.get("/v1/resume")

    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4"
    form = parse_qs(token_route.calls.last.request.content.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    header_b64, payload_b64, sig_b64 = form["assertion"][0].split(".")
    assert json.loads(_b64url_decode(header_b64)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_b64url_decode(payload_b64))
    assert claims["iss"] == _SVC_EMAIL
    assert claims["aud"] == _TOKEN_URL
    assert claims["scope"] == "https://www.googleapis.com/auth/drive"
    assert claims["exp"] - claims["iat"] == 3600
    # Raises InvalidSignature if the signature does not match the key.
    key.public_key().verify(
        _b64url_decode(sig_b64),
        f"{header_b64}.{payload_b64}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    # The Drive calls carry the exchanged token.
    drive_call = respx.calls.last.request
    assert drive_call.headers["Authorization"] == "Bearer tok-1"
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_resume_reuses_a_cached_token_until_near_expiry(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    _configure(monkeypatch, _pem(_rsa_key()))
    token_route = respx.post(_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    _mock_drive()

    first = await client.get("/v1/resume")
    second = await client.get("/v1/resume")

    assert first.status_code == 200
    assert second.status_code == 200
    assert token_route.call_count == 1
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_resume_refreshes_a_token_inside_the_expiry_margin(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    """A token with under a minute left is treated as expired, not reused."""
    _configure(monkeypatch, _pem(_rsa_key()))
    token_route = respx.post(_TOKEN_URL).mock(
        side_effect=[
            Response(200, json={"access_token": "short", "expires_in": 30}),
            Response(200, json={"access_token": "fresh", "expires_in": 3600}),
        ]
    )
    _mock_drive()

    await client.get("/v1/resume")
    resp = await client.get("/v1/resume")

    assert resp.status_code == 200
    assert token_route.call_count == 2
    assert respx.calls.last.request.headers["Authorization"] == "Bearer fresh"
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_resume_502_when_oauth_exchange_fails(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    _configure(monkeypatch, _pem(_rsa_key()))
    respx.post(_TOKEN_URL).mock(return_value=Response(400, json={"error": "nope"}))
    drive = respx.get(_META_URL).mock(return_value=Response(200, json={}))

    resp = await client.get("/v1/resume")

    assert resp.status_code == 502
    assert resp.json()["error"] == {
        "code": "upstream_error",
        "message": "Google OAuth token exchange failed",
        "details": None,
    }
    assert not drive.called
    get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["GOOGLE_CLIENT_EMAIL", "GOOGLE_PRIVATE_KEY"])
@respx.mock
async def test_resume_502_when_service_account_not_configured(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient, missing: str
) -> None:
    _configure(monkeypatch, _pem(_rsa_key()))
    monkeypatch.setenv(missing, "")
    get_settings.cache_clear()
    token_route = respx.post(_TOKEN_URL).mock(return_value=Response(200, json={}))

    resp = await client.get("/v1/resume")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"
    assert "not configured" in resp.json()["error"]["message"]
    assert not token_route.called
    get_settings.cache_clear()


def test_sign_jwt_refuses_a_non_rsa_key() -> None:
    from kaianolevine_api.routers import resume as resume_mod

    ec_pem = _pem(ec.generate_private_key(ec.SECP256R1()))
    with pytest.raises(ValueError, match="RS256 needs an RSA private key"):
        resume_mod._sign_jwt_rs256(ec_pem, {"iss": "x"})
