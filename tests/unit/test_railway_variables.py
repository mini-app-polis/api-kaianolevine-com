"""Every name the production service reads reaches it.

Doppler syncs secrets to Railway's shared variables, but the service only
sees the names .railway/railway.ts declares, and the next apply removes any
variable the file does not declare. A machine declared in identity_registry
without its key listed there is reconciled, holds its roles, and still
answers 401 to every request — which is how wiki-curator-deploy shipped. A
Settings field missing from the file is the same failure one step later:
whatever value it holds on the service is deleted by the next apply.
"""

from __future__ import annotations

import re
from pathlib import Path

from kaianolevine_api import identity_registry
from kaianolevine_api.config import Settings

RAILWAY_TS = Path(__file__).resolve().parents[2] / ".railway" / "railway.ts"

#: Declared everywhere but keyed in development only (CD-033): in production
#: they are meant not to authenticate.
DEVELOPMENT_ONLY = {m.name for m in identity_registry.MACHINES if m.development_only}

#: Settings fields deliberately left out of railway.ts, each with the reason.
#: Only a field with a default can be here: the value in config.py is the
#: reviewed one, and it is not set per environment. A field that production
#: sets — a secret, a URL, or a switch the docs say is flipped without a
#: deploy — is declared in railway.ts instead.
NOT_DECLARED: dict[str, str] = {
    "HTTP_CLIENT_TIMEOUT_SECS": "overridden by tests only",
    "GITHUB_DEFAULT_BRANCH": "fallback for payloads without a default branch",
    "SPOTIFY_DOPPLER_PROJECT": "the code default is the production project",
    "SPOTIFY_DOPPLER_CONFIG": "the code default is the production config",
    "SPOTIFY_TOKEN_CHECK_INTERVAL_SECS": "code default, reviewed in config.py",
    "WCS_QA_EMBEDDING_MODEL": "versioned with the embeddings, changed in code",
    "WCS_QA_CHUNKING_VERSION": "versioned with the embeddings, changed in code",
    "WCS_QA_PAGE_FLATTENER_VERSION": "versioned with the embeddings, changed in code",
    "WCS_QA_REFRESH_DEBOUNCE_SECS": "code default, reviewed in config.py",
    "WCS_QA_AGENT_MODEL": "code default, reviewed in config.py",
    "WCS_QA_JUDGE_MODEL": "code default, reviewed in config.py",
    "WCS_QA_MAX_TOOL_CALLS_DEFAULT": "agent budget, reviewed in config.py",
    "WCS_QA_MAX_TOOL_CALLS_LIMIT": "agent budget, reviewed in config.py",
    "WCS_QA_MAX_INPUT_TOKENS_DEFAULT": "agent budget, reviewed in config.py",
    "WCS_QA_MAX_INPUT_TOKENS_LIMIT": "agent budget, reviewed in config.py",
    "WCS_QA_MAX_OUTPUT_TOKENS_DEFAULT": "agent budget, reviewed in config.py",
    "WCS_QA_MAX_OUTPUT_TOKENS_LIMIT": "agent budget, reviewed in config.py",
    "WCS_SITE_URL": "the one public site, reviewed in config.py",
}


def _shared_names() -> set[str]:
    text = RAILWAY_TS.read_text()
    block = text.split("const SHARED = [", 1)[1].split("];", 1)[0]
    return set(re.findall(r'"([A-Z0-9_]+)"', block))


def _env_keys() -> set[str]:
    """Names declared directly in the service's env block (preserve() etc.)."""
    text = RAILWAY_TS.read_text()
    block = text.split("env: {", 1)[1].split("\n    },", 1)[0]
    return set(re.findall(r"^\s+([A-Z0-9_]+):", block, flags=re.MULTILINE))


def _declared_names() -> set[str]:
    return _shared_names() | _env_keys()


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


def test_the_parser_sees_both_kinds_of_declaration() -> None:
    # Guards the two parsers above: if either stops matching, every check
    # below would pass vacuously or fail for the wrong reason.
    assert "DATABASE_URL" in _env_keys()
    assert "ENVIRONMENT" in _shared_names()


def test_every_settings_field_is_declared_or_deliberately_left_out() -> None:
    declared = _declared_names()
    missing = sorted(
        name
        for name in Settings.model_fields
        if name not in declared and name not in NOT_DECLARED
    )
    assert missing == [], (
        "Settings fields missing from .railway/railway.ts; the next apply "
        "removes them from the service. Declare each there, or list it in "
        f"NOT_DECLARED with a reason: {missing}"
    )


def test_the_not_declared_list_names_only_optional_undeclared_fields() -> None:
    fields = Settings.model_fields
    declared = _declared_names()
    for name in NOT_DECLARED:
        assert name in fields, f"{name} is no longer a Settings field"
        assert not fields[name].is_required(), f"{name} has no default"
        assert name not in declared, f"{name} is declared in railway.ts"
