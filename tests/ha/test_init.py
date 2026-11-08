from typing import Any

import httpx
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka
from tests.ha.conftest import SetupIntegration


def account_entry(*, minor_version: int = 2) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        minor_version=minor_version,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )


def stored_session(hass_storage: dict[str, Any], entry_id: str) -> dict[str, Any]:
    return hass_storage[f"bilkatogo.{entry_id}.session"]["data"]


async def test_session_is_kept_in_home_assistant_storage(
    hass: HomeAssistant, setup_integration: SetupIntegration, hass_storage: dict[str, Any]
) -> None:
    entry = await setup_integration()

    session = stored_session(hass_storage, entry.entry_id)
    assert session["email"] == EMAIL
    assert session["jwt"] == "eyFake.jwt.token"


async def test_reload_reuses_the_stored_session(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    entry = await setup_integration()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert len(bilka.calls("gigya_login")) == 1
    assert len(bilka.calls("order_history")) == 2


async def test_setup_retries_when_bilka_is_unreachable(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    bilka.script("gigya_login", httpx.ConnectError("down"))

    entry = await setup_integration()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_links_to_the_bilkatogo_website(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    entry = await setup_integration()

    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)

    assert device.identifiers == {(DOMAIN, EMAIL)}
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.configuration_url == "https://www.bilkatogo.dk"


async def test_entities_are_keyed_on_the_account(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    entry = await setup_integration()

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)

    assert {entity.unique_id for entity in entities} == {
        f"{EMAIL}_{key}"
        for key in (
            "order_status",
            "order_total",
            "pickup_slot",
            "pickup_point",
            "pickup_code",
            "substitutions",
            "missing_items",
            "next_pickup_slot",
            "ready_for_pickup",
            "pickup_slots",
            "substitution",
            "missing_item",
        )
    }


async def test_re_adding_the_account_keeps_entity_settings(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    entry = await setup_integration()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{EMAIL}_order_status")
    assert entity_id is not None
    registry.async_update_entity(entity_id, new_entity_id="sensor.bilka", name="Bilka")
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    again = account_entry()
    again.add_to_hass(hass)
    await hass.config_entries.async_setup(again.entry_id)
    await hass.async_block_till_done()

    entity = registry.async_get("sensor.bilka")
    assert entity is not None
    assert entity.config_entry_id == again.entry_id
    assert entity.name == "Bilka"


async def test_entities_and_device_move_from_the_entry_to_the_account(
    hass: HomeAssistant,
) -> None:
    entry = account_entry(minor_version=1)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    old = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_order_status",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="my_order_status",
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    entity = er.async_get(hass).async_get(old.entity_id)
    assert entity is not None
    assert entity.unique_id == f"{EMAIL}_order_status"
    assert entity.device_id == device.id
    [migrated] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert migrated.id == device.id
    assert migrated.identifiers == {(DOMAIN, EMAIL)}


async def test_unload_and_remove_entry(
    hass: HomeAssistant, setup_integration: SetupIntegration, hass_storage: dict[str, Any]
) -> None:
    entry = await setup_integration()

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"bilkatogo.{entry.entry_id}.session" not in hass_storage
