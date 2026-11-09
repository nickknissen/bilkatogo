from datetime import timedelta

import httpx
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import FakeBilka
from tests.ha.conftest import SetupIntegration


def ready_for_pickup(hass: HomeAssistant, entry: MockConfigEntry) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.unique_id}_ready_for_pickup"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def only_order(bilka: FakeBilka, status_code: str, *, home_delivery: bool = False) -> None:
    order = bilka.orders[0]
    bilka.orders = [
        {
            **order,
            "status": status_code,
            "pickupzone": {**order["pickupzone"], "home_delivery": home_delivery},
        }
    ]


@pytest.mark.parametrize("home_delivery", [False, True], ids=["pickup", "home_delivery"])
async def test_on_while_the_current_order_is_packed(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka, home_delivery: bool
) -> None:
    only_order(bilka, "Pakket", home_delivery=home_delivery)

    entry = await setup_integration()

    state = ready_for_pickup(hass, entry)
    assert state.state == STATE_ON
    assert "device_class" not in state.attributes


@pytest.mark.parametrize(
    "status",
    ["Ubehandlet", "Bestilt", "Leveret", "Uafhentet", "Annulleret", "Fuldt udsolgt", "Noget nyt"],
)
async def test_off_for_every_other_order_status(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka, status: str
) -> None:
    only_order(bilka, status)

    entry = await setup_integration()

    assert ready_for_pickup(hass, entry).state == STATE_OFF


async def test_off_without_a_current_order(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    bilka.orders = []

    entry = await setup_integration()

    assert ready_for_pickup(hass, entry).state == STATE_OFF


async def test_unavailable_when_the_update_fails(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = await setup_integration()
    bilka.script("order_history", httpx.ConnectError("down"))

    freezer.tick(timedelta(minutes=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert ready_for_pickup(hass, entry).state == STATE_UNAVAILABLE


async def test_name_is_translated_in_danish_and_english(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    await setup_integration()
    key = f"component.{DOMAIN}.entity.binary_sensor.ready_for_pickup.name"

    da = await async_get_translations(hass, "da", "entity", [DOMAIN])
    en = await async_get_translations(hass, "en", "entity", [DOMAIN])

    assert en[key] == "Ready for pickup"
    assert da[key] == "Klar til afhentning"
