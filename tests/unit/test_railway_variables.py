"""Every declared machine's key reaches the production service.

Doppler syncs secrets to Railway's shared variables, but the service only
sees the names .railway/railway.ts references. A machine declared in
identity_registry without its key listed there is reconciled, holds its
roles, and still answers 401 to every request — which is how
wiki-curator-deploy shipped.
"""

from __future__ import annotations

import re
from pathlib import Path

from kaianolevine_api import identity_registry

RAILWAY_TS = Path(__file__).resolve().parents[2] / ".railway" / "railway.ts"

#: Declared everywhere but keyed in development only (CD-033): in production
#: it is meant not to authenticate.
DEVELOPMENT_ONLY = {"contract-suite"}


def _shared_names() -> set[str]:
    text = RAILWAY_TS.read_text()
    block = text.split("const SHARED = [", 1)[1].split("];", 1)[0]
    return set(re.findall(r'"([A-Z0-9_]+)"', block))


def test_every_production_machine_key_is_referenced_by_the_service() -> None:
    shared = _shared_names()
    missing = sorted(
        m.key_env_var
        for m in identity_registry.MACHINES
        if m.name not in DEVELOPMENT_ONLY and m.key_env_var not in shared
    )
    assert missing == []


def test_a_development_only_machine_is_not_keyed_in_production() -> None:
    shared = _shared_names()
    for name in DEVELOPMENT_ONLY:
        machine = identity_registry.declared(name)
        assert machine is not None
        assert machine.key_env_var not in shared
