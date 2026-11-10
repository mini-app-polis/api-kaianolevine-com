from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import FeatureFlag as DbFeatureFlag
from kaianolevine_api.services.flags import is_enabled


async def test_flags_get_list_meta_total_matches_db_count(client, async_engine) -> None:
    sessionmaker = async_sessionmaker(
        async_engine, expire_on_commit=False, autoflush=False
    )
    async with sessionmaker() as session:
        db_total = (
            await session.execute(select(func.count()).select_from(DbFeatureFlag))
        ).scalar_one()

    list_resp = await client.get("/v1/flags")
    assert list_resp.status_code == 200
    list_json = list_resp.json()
    assert list_json["meta"]["total"] == db_total
    assert list_json["meta"]["count"] == len(list_json["data"])
    assert list_json["meta"]["total"] == list_json["meta"]["count"]


async def test_flags_patch_known_flag_and_service_contract(
    client, async_engine
) -> None:
    sessionmaker = async_sessionmaker(
        async_engine, expire_on_commit=False, autoflush=False
    )
    async with sessionmaker() as session:
        assert await is_enabled("flags.deejay_api.ingest_enabled", session) is True

    patch_resp = await client.patch(
        "/v1/flags/flags.deejay_api.ingest_enabled",
        json={"enabled": False},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()["data"]
    assert patched["name"] == "flags.deejay_api.ingest_enabled"
    assert patched["enabled"] is False

    async with sessionmaker() as session:
        assert await is_enabled("flags.deejay_api.unknown_flag", session) is False
        assert await is_enabled("flags.deejay_api.ingest_enabled", session) is False


async def test_flags_value_null_for_existing_and_settable_via_patch(client) -> None:
    name = "flags.deejay_api.ingest_enabled"

    def item(resp):
        return next(i for i in resp.json()["data"] if i["name"] == name)

    assert item(await client.get("/v1/flags"))["value"] is None

    set_resp = await client.patch(
        f"/v1/flags/{name}", json={"enabled": True, "value": "dry"}
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["data"]["value"] == "dry"
    assert item(await client.get("/v1/flags"))["value"] == "dry"

    # An omitted value is left as it is.
    keep_resp = await client.patch(f"/v1/flags/{name}", json={"enabled": False})
    assert keep_resp.json()["data"]["value"] == "dry"
    assert keep_resp.json()["data"]["enabled"] is False

    # An explicit null clears it.
    clear_resp = await client.patch(
        f"/v1/flags/{name}", json={"enabled": True, "value": None}
    )
    assert clear_resp.json()["data"]["value"] is None


_DEEJAY_COG_STEP_MODES = {
    "library_scan": "write",
    "library_trash": "off",
    "library_identify": "write",
    "library_backfill": "write",
    "library_sync": "write",
    "library_duplicates": "write",
    "library_reconcile": "write",
    "new_music": "write",
}


async def test_deejay_cog_step_flags_are_seeded_with_their_modes(client) -> None:
    listed = {i["name"]: i for i in (await client.get("/v1/flags")).json()["data"]}

    for step, mode in _DEEJAY_COG_STEP_MODES.items():
        flag = listed[f"flags.deejay_cog.{step}"]
        assert flag["value"] == mode, step
        assert flag["enabled"] is (mode != "off"), step


async def test_flags_patch_unknown_name_returns_404(client) -> None:
    resp = await client.patch(
        "/v1/flags/does-not-exist-flag-name",
        json={"enabled": True},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
