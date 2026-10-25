"""Machines authenticate with named keys, and exist before they call.

The key identifies the machine, so identity is knowable from the declaration
plus configuration alone. Principals are created at boot rather than
discovered on first contact, and nothing the caller sends influences who it is
or what it may do.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from identity.apikey import API_KEY_ISSUER, MachineKey
from identity.errors import CredentialInvalid
from identity.store import Principal, PrincipalRole
from identity.store.models import AuditEventRow
from identity.types import VerifiedSubject
from sqlalchemy import select, text
from starlette.datastructures import State

from kaianolevine_api import auth as auth_mod
from kaianolevine_api import identity_registry as reg
from kaianolevine_api.auth import build_verifier, require_scope


class _Settings:
    CLERK_JWKS_URL = None
    CLERK_ISSUER = None
    CLERK_ISSUERS = None


class _Req:
    path_params: dict[str, str] = {}
    headers: dict[str, str] = {}

    def __init__(self) -> None:
        # The guard stamps the caller here for the notification feed to read.
        self.state = State()


async def _seeded(session):
    """Issuer + role vocabulary, as migrations 023/024 leave them."""
    from tests.integration.conftest import seed_identity

    await seed_identity(session)


async def _roles_of(session, subject: str) -> set[str]:
    row = (
        (
            await session.execute(
                select(Principal).where(
                    Principal.issuer == API_KEY_ISSUER, Principal.subject == subject
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return set()
    return set(
        (
            await session.execute(
                select(PrincipalRole.role_name).where(
                    PrincipalRole.principal_id == row.id
                )
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Configuration -> identity
# ---------------------------------------------------------------------------


def test_key_env_var_is_derived_from_the_name() -> None:
    """One convention, so there is no second thing to keep in step."""
    assert reg.Machine(name="deejay-cog", roles=()).key_env_var == "DEEJAY_COG_API_KEY"


def test_machine_without_a_key_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing secret must mean 'cannot authenticate', not 'empty key'."""
    monkeypatch.setattr(reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=()),))
    assert reg.machine_keys({}) == []
    assert reg.machine_keys({"DEEJAY_COG_API_KEY": "   "}) == []


def test_previous_key_env_var_is_derived_from_the_name() -> None:
    machine = reg.Machine(name="deejay-cog", roles=())
    assert machine.previous_key_env_var == "DEEJAY_COG_API_KEY_PREVIOUS"


def test_previous_key_is_read_beside_the_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=()),))
    assert reg.machine_keys(
        {"DEEJAY_COG_API_KEY": "k_new", "DEEJAY_COG_API_KEY_PREVIOUS": " k_old "}
    ) == [MachineKey(name="deejay-cog", key="k_new", previous="k_old")]


@pytest.mark.parametrize("env", [{}, {"DEEJAY_COG_API_KEY_PREVIOUS": "   "}])
def test_unset_or_blank_previous_key_means_none(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    """No previous key is today's behaviour: the current key alone proves it."""
    monkeypatch.setattr(reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=()),))
    keys = reg.machine_keys({"DEEJAY_COG_API_KEY": "k_new", **env})
    assert keys == [MachineKey(name="deejay-cog", key="k_new")]
    assert keys[0].previous is None


def test_previous_key_alone_does_not_authenticate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing current key is a missing secret, not a rotation."""
    monkeypatch.setattr(reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=()),))
    assert reg.machine_keys({"DEEJAY_COG_API_KEY_PREVIOUS": "k_old"}) == []


class _Warnings:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str) -> None:
        self.messages.append(message)


@pytest.mark.parametrize(
    ("environment", "warned"), [("production", False), ("development", True)]
)
def test_development_only_machine_warns_only_outside_production(
    monkeypatch: pytest.MonkeyPatch, environment: str, warned: bool
) -> None:
    """In production its missing key is the guard, not a missing secret."""
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setattr(
        reg,
        "MACHINES",
        (reg.Machine(name="contract-suite", roles=(), development_only=True),),
    )
    log = _Warnings()
    monkeypatch.setattr(reg, "_log", log)
    assert reg.machine_keys({}) == []
    assert bool(log.messages) is warned


def test_missing_key_still_warns_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=()),))
    log = _Warnings()
    monkeypatch.setattr(reg, "_log", log)
    assert reg.machine_keys({}) == []
    assert log.messages


async def test_key_identifies_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    verifier = build_verifier(_Settings(), {"DEEJAY_COG_API_KEY": "k_secret"})
    subject = await verifier.verify("k_secret")
    assert subject.subject == "deejay-cog"
    assert subject.kind == "machine"
    assert subject.issuer == API_KEY_ISSUER


async def test_either_key_identifies_the_machine_and_says_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=()),))
    verifier = build_verifier(
        _Settings(),
        {"DEEJAY_COG_API_KEY": "k_new", "DEEJAY_COG_API_KEY_PREVIOUS": "k_old"},
    )
    current = await verifier.verify("k_new")
    previous = await verifier.verify("k_old")
    assert (current.subject, current.key_slot) == ("deejay-cog", "current")
    assert (previous.subject, previous.key_slot) == ("deejay-cog", "previous")


async def test_without_a_previous_key_the_old_one_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=()),))
    verifier = build_verifier(_Settings(), {"DEEJAY_COG_API_KEY": "k_new"})
    assert (await verifier.verify("k_new")).key_slot == "current"
    with pytest.raises(CredentialInvalid):
        await verifier.verify("k_old")


async def test_unknown_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=()),))
    verifier = build_verifier(_Settings(), {"DEEJAY_COG_API_KEY": "k_secret"})
    with pytest.raises(CredentialInvalid):
        await verifier.verify("k_wrong")


async def test_one_machines_key_never_identifies_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Impersonation requires possessing the key, not claiming the name."""
    monkeypatch.setattr(
        reg,
        "MACHINES",
        (
            reg.Machine(name="deejay-cog", roles=()),
            reg.Machine(name="watcher-cog", roles=()),
        ),
    )
    verifier = build_verifier(
        _Settings(),
        {"DEEJAY_COG_API_KEY": "k_dj", "WATCHER_COG_API_KEY": "k_watch"},
    )
    assert (await verifier.verify("k_watch")).subject == "watcher-cog"
    assert (await verifier.verify("k_dj")).subject == "deejay-cog"


# ---------------------------------------------------------------------------
# Declaration -> principals, at boot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_creates_principals_before_any_request(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No first-request special case: the machine exists after deploy."""
    await _seeded(db_session)
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    result = await reg.reconcile(db_session)
    assert result["created"] == 1
    assert result["granted"] == 1
    assert await _roles_of(db_session, "deejay-cog") == {"catalog-ingest"}


@pytest.mark.asyncio
async def test_reconcile_is_idempotent(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seeded(db_session)
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    await reg.reconcile(db_session)
    assert await reg.reconcile(db_session) == {
        "created": 0,
        "granted": 0,
        "revoked": 0,
    }


@pytest.mark.asyncio
async def test_removing_a_machine_revokes_but_keeps_the_row(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The principal survives so historical audit events still resolve."""
    await _seeded(db_session)
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    await reg.reconcile(db_session)
    monkeypatch.setattr(reg, "MACHINES", ())
    assert (await reg.reconcile(db_session))["revoked"] == 1
    assert await _roles_of(db_session, "deejay-cog") == set()
    still_there = (
        (
            await db_session.execute(
                select(Principal).where(Principal.subject == "deejay-cog")
            )
        )
        .scalars()
        .first()
    )
    assert still_there is not None


@pytest.mark.asyncio
async def test_missing_issuer_row_is_reported_not_guessed(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without migration 024 there is nothing to hang principals off."""
    from identity.store import Issuer

    await _seeded(db_session)
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    row = (
        (
            await db_session.execute(
                select(Issuer).where(Issuer.issuer == API_KEY_ISSUER)
            )
        )
        .scalars()
        .first()
    )
    if row is not None:
        await db_session.delete(row)
        await db_session.commit()
    assert await reg.reconcile(db_session) == {
        "created": 0,
        "granted": 0,
        "revoked": 0,
    }


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_authenticated_machine_is_authorized_and_audited(
    client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    await reg.reconcile(db_session)
    monkeypatch.setattr(
        auth_mod,
        "verify_bearer",
        AsyncMock(
            return_value=VerifiedSubject(
                issuer=API_KEY_ISSUER, subject="deejay-cog", kind="machine"
            )
        ),
    )
    from kaianolevine_api.config import get_settings

    principal = await require_scope("catalog.sets.write")(
        _Req(),  # type: ignore[arg-type]
        authorization="Bearer k_secret",
        settings=get_settings(),
        session=db_session,
    )
    assert principal.display_name == "deejay-cog"

    rows = (await db_session.execute(select(AuditEventRow))).scalars().all()
    assert rows[-1].allowed is True
    assert rows[-1].reason == "granted_by_role"
    assert rows[-1].subject == "deejay-cog"


async def _audited_key_slots(session) -> list[tuple[str | None, str | None]]:
    rows = await session.execute(
        text("SELECT subject, key_slot FROM identity_audit_events ORDER BY occurred_at")
    )
    return [(subject, slot) for subject, slot in rows.all()]


@pytest.mark.parametrize("slot", ["current", "previous"])
@pytest.mark.asyncio
async def test_audit_event_records_which_key_matched(
    client, db_session, monkeypatch: pytest.MonkeyPatch, slot: str
) -> None:
    """Retiring a previous key reads this: per machine, with the time."""
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    await reg.reconcile(db_session)
    monkeypatch.setattr(
        auth_mod,
        "verify_bearer",
        AsyncMock(
            return_value=VerifiedSubject(
                issuer=API_KEY_ISSUER,
                subject="deejay-cog",
                kind="machine",
                key_slot=slot,  # type: ignore[arg-type]
            )
        ),
    )
    from kaianolevine_api.config import get_settings

    await require_scope("catalog.sets.write")(
        _Req(),  # type: ignore[arg-type]
        authorization="Bearer k_secret",
        settings=get_settings(),
        session=db_session,
    )
    # A denial is recorded with its slot too: a key that is still presented
    # is still in use, whatever it was refused.
    with pytest.raises(Exception):
        await require_scope("wcs.notes.write")(
            _Req(),  # type: ignore[arg-type]
            authorization="Bearer k_secret",
            settings=get_settings(),
            session=db_session,
        )
    assert await _audited_key_slots(db_session) == [
        ("deejay-cog", slot),
        ("deejay-cog", slot),
    ]


@pytest.mark.asyncio
async def test_audit_event_without_a_key_slot_records_none(
    client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only named-key verification reports a slot; nothing is invented."""
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    await reg.reconcile(db_session)
    monkeypatch.setattr(
        auth_mod,
        "verify_bearer",
        AsyncMock(
            return_value=VerifiedSubject(
                issuer=API_KEY_ISSUER, subject="deejay-cog", kind="machine"
            )
        ),
    )
    from kaianolevine_api.config import get_settings

    await require_scope("catalog.sets.write")(
        _Req(),  # type: ignore[arg-type]
        authorization="Bearer k_secret",
        settings=get_settings(),
        session=db_session,
    )
    assert await _audited_key_slots(db_session) == [("deejay-cog", None)]


@pytest.mark.asyncio
async def test_declared_roles_bound_the_machine(
    client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid key grants exactly the declaration and nothing more."""
    monkeypatch.setattr(
        reg, "MACHINES", (reg.Machine(name="deejay-cog", roles=("catalog-ingest",)),)
    )
    await reg.reconcile(db_session)
    monkeypatch.setattr(
        auth_mod,
        "verify_bearer",
        AsyncMock(
            return_value=VerifiedSubject(
                issuer=API_KEY_ISSUER, subject="deejay-cog", kind="machine"
            )
        ),
    )
    from kaianolevine_api.config import get_settings

    with pytest.raises(Exception) as exc:
        await require_scope("wcs.notes.write")(
            _Req(),  # type: ignore[arg-type]
            authorization="Bearer k_secret",
            settings=get_settings(),
            session=db_session,
        )
    assert getattr(exc.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_unknown_machine_resolves_to_nothing(
    client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key that authenticated but has no principal is a config problem."""
    monkeypatch.setattr(reg, "MACHINES", ())
    monkeypatch.setattr(
        auth_mod,
        "verify_bearer",
        AsyncMock(
            return_value=VerifiedSubject(
                issuer=API_KEY_ISSUER, subject="ghost-cog", kind="machine"
            )
        ),
    )
    from kaianolevine_api.config import get_settings

    with pytest.raises(Exception) as exc:
        await require_scope("catalog.sets.write")(
            _Req(),  # type: ignore[arg-type]
            authorization="Bearer k",
            settings=get_settings(),
            session=db_session,
        )
    assert getattr(exc.value, "status_code", None) == 403
    rows = (await db_session.execute(select(AuditEventRow))).scalars().all()
    assert rows[-1].reason == "principal_not_found"
    assert uuid.UUID(str(rows[-1].event_id))
