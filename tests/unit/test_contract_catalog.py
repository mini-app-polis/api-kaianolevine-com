"""The API serves every endpoint in the fleet's contract catalog.

The catalog (mini_app_polis.api.contract) is what the cogs call through the
shared client, and this API takes its models from it. The live contract
suite skips an endpoint the development API does not serve yet, since the
catalog is released before the API can serve a new one. So a route this API
drops, or moves, has to fail here, before it deploys.
"""

from __future__ import annotations

import re

from mini_app_polis.api.contract import ENDPOINTS

from kaianolevine_api.main import app

_PATH_PARAMETER = re.compile(r"\{[^}]*\}")


def _route(method: str, path: str) -> tuple[str, str]:
    return method.upper(), _PATH_PARAMETER.sub("{}", path)


def test_every_catalog_endpoint_is_a_route_this_api_serves() -> None:
    served = {
        _route(method, path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }

    missing = [
        f"{e.name}: {e.method} {e.path}"
        for e in ENDPOINTS
        if _route(e.method, e.path) not in served
    ]

    assert missing == []
