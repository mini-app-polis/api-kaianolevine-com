"""Every method a route answers is one a browser may send across origins.

A method the CORS middleware leaves out fails its preflight with a 400, so
the browser never sends the request at all.
"""

from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from kaianolevine_api.main import app


def test_cors_allows_every_method_a_route_answers() -> None:
    (cors,) = (m for m in app.user_middleware if m.cls is CORSMiddleware)
    allowed = set(cors.kwargs["allow_methods"])
    used = {
        method
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    } - {"HEAD", "OPTIONS"}

    assert used <= allowed, f"not allowed across origins: {sorted(used - allowed)}"
