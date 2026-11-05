"""The Issue writer: the App's token, one open Issue per marker, refusals.

GitHub is stubbed at the HTTP boundary by respx; the App key is generated
for the test.
"""

from __future__ import annotations

import json

import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import github_issues

GH = "https://api.github.com"
REPO = "mini-app-polis/infra"
MARKER = "ci/mini-app-polis/infra/CI@main"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    .decode()
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SIGNALS_APP_CLIENT_ID", "Iv-signals")
    # Held in one line, as a secret store keeps it.
    monkeypatch.setenv("SIGNALS_APP_PRIVATE_KEY", PRIVATE_PEM.replace("\n", "\\n"))
    get_settings.cache_clear()
    github_issues._tokens.clear()
    yield get_settings()
    get_settings.cache_clear()
    github_issues._tokens.clear()


def _app_token_routes() -> tuple[respx.Route, respx.Route]:
    installation = respx.get(f"{GH}/repos/{REPO}/installation").mock(
        return_value=Response(200, json={"id": 42})
    )
    token = respx.post(f"{GH}/app/installations/42/access_tokens").mock(
        return_value=Response(
            201, json={"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"}
        )
    )
    return installation, token


def test_the_app_jwt_is_issued_by_the_client_id_and_signed_with_its_key(
    settings,
) -> None:
    token = github_issues.app_jwt(settings, now=1_700_000_000)
    claims = jwt.decode(
        token,
        PUBLIC_PEM,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == "Iv-signals"
    assert claims["iat"] == 1_700_000_000 - 60
    assert claims["exp"] - claims["iat"] <= 600


def test_without_the_app_nothing_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGNALS_APP_CLIENT_ID", raising=False)
    monkeypatch.delenv("SIGNALS_APP_PRIVATE_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(github_issues.IssueError, match="not configured"):
            github_issues.app_jwt(get_settings())
    finally:
        get_settings.cache_clear()


@respx.mock
@pytest.mark.asyncio
async def test_an_issue_is_opened_with_its_marker(settings) -> None:
    _app_token_routes()
    respx.get(f"{GH}/repos/{REPO}/issues").mock(return_value=Response(200, json=[]))
    created = respx.post(f"{GH}/repos/{REPO}/issues").mock(
        return_value=Response(
            201, json={"number": 5, "html_url": f"https://github.com/{REPO}/issues/5"}
        )
    )

    issue = await github_issues.open_issue(
        REPO,
        marker=MARKER,
        title="infra: CI failing on main",
        body="b",
        settings=settings,
    )

    assert issue == github_issues.Issue(REPO, 5, f"https://github.com/{REPO}/issues/5")
    sent = json.loads(created.calls.last.request.content)
    assert sent["title"] == "infra: CI failing on main"
    assert sent["body"].endswith(github_issues.marker_comment(MARKER))
    assert created.calls.last.request.headers["Authorization"] == "Bearer ghs_test"


@respx.mock
@pytest.mark.asyncio
async def test_an_issue_already_open_for_the_marker_is_returned(settings) -> None:
    _app_token_routes()
    respx.get(f"{GH}/repos/{REPO}/issues").mock(
        return_value=Response(
            200,
            json=[
                {"number": 1, "html_url": "u1", "body": "x", "pull_request": {}},
                {
                    "number": 3,
                    "html_url": f"https://github.com/{REPO}/issues/3",
                    "body": f"b\n\n{github_issues.marker_comment(MARKER)}",
                },
            ],
        )
    )
    created = respx.post(f"{GH}/repos/{REPO}/issues")

    issue = await github_issues.open_issue(
        REPO, marker=MARKER, title="t", body="b", settings=settings
    )

    assert issue.number == 3
    assert not created.called


@respx.mock
@pytest.mark.asyncio
async def test_the_installation_token_is_reused(settings) -> None:
    installation, token = _app_token_routes()
    respx.post(f"{GH}/repos/{REPO}/issues/5/comments").mock(
        return_value=Response(201, json={})
    )

    await github_issues.comment(REPO, 5, "one", settings=settings)
    await github_issues.comment(REPO, 5, "two", settings=settings)

    assert installation.call_count == token.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_closing_comments_then_closes_as_completed(settings) -> None:
    _app_token_routes()
    commented = respx.post(f"{GH}/repos/{REPO}/issues/5/comments").mock(
        return_value=Response(201, json={})
    )
    patched = respx.patch(f"{GH}/repos/{REPO}/issues/5").mock(
        return_value=Response(200, json={})
    )

    await github_issues.close(REPO, 5, body="Recovered.", settings=settings)

    assert json.loads(commented.calls.last.request.content) == {"body": "Recovered."}
    assert json.loads(patched.calls.last.request.content) == {
        "state": "closed",
        "state_reason": "completed",
    }


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "expected"), [("open", True), ("closed", False)])
async def test_an_issues_state_is_read_from_github(
    settings, state: str, expected: bool
) -> None:
    _app_token_routes()
    respx.get(f"{GH}/repos/{REPO}/issues/5").mock(
        return_value=Response(200, json={"number": 5, "state": state})
    )

    assert await github_issues.is_open(REPO, 5, settings=settings) is expected


@respx.mock
@pytest.mark.asyncio
async def test_a_refusal_raises_an_issue_error(settings) -> None:
    respx.get(f"{GH}/repos/{REPO}/installation").mock(return_value=Response(404))

    with pytest.raises(github_issues.IssueError, match="404"):
        await github_issues.comment(REPO, 5, "x", settings=settings)
