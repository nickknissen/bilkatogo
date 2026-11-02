from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import FakeBilka, load_fixture
from tests.ha.conftest import SetupIntegration

ORDER_NUMBER = "900000000004"

BREAKDOWN = {
    "deposit": 1.5,
    "service_fee": 39.0,
    "packaging": 0.0,
    "delivery_fee": 0.0,
    "discount": 0.0,
}


def order_total(hass: HomeAssistant, entry: MockConfigEntry) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_order_total"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def set_status(bilka: FakeBilka, status_code: str, packed_price: int) -> None:
    bilka.orders = [
        {
            **bilka.orders[0],
            "orderId": ORDER_NUMBER,
            "status": status_code,
            "orderedPrice": 80000,
            "packedPrice": packed_price,
        }
    ]
    details = bilka.details[ORDER_NUMBER]
    bilka.details[ORDER_NUMBER] = {"order": {**details["order"], "status": status_code}}


async def next_update(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_order_total_from_received_to_picked_up(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    set_status(bilka, "Bestilt", 79900)
    entry = await setup_integration()

    state = order_total(hass, entry)
    assert state.state == "799.00"
    assert state.attributes["device_class"] == "monetary"
    assert state.attributes["unit_of_measurement"] == "DKK"
    assert state.attributes["ordered_total"] == 800.0
    assert state.attributes["difference"] == -1.0
    assert not BREAKDOWN.keys() & state.attributes.keys()

    set_status(bilka, "Pakket", 79380)
    await next_update(hass, freezer)
    state = order_total(hass, entry)
    assert state.state == "793.80"
    assert state.attributes["ordered_total"] == 800.0
    assert state.attributes["difference"] == -6.2
    assert {key: state.attributes[key] for key in BREAKDOWN} == BREAKDOWN

    set_status(bilka, "Faktureret", 79380)
    await next_update(hass, freezer)
    state = order_total(hass, entry)
    assert state.state == "793.80"
    assert {key: state.attributes[key] for key in BREAKDOWN} == BREAKDOWN

    assert len(bilka.calls("order_history")) == 3
    assert len(bilka.calls("order_details")) == 1


async def test_received_order_total_is_bilkas_estimate(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    bilka.orders = load_fixture("order_history_received.json")["orders"]
    entry = await setup_integration()

    state = order_total(hass, entry)
    assert state.state == "859.98"
    assert state.attributes["ordered_total"] == 876.53
    assert state.attributes["difference"] == -16.55


async def test_order_total_is_translated_in_danish_and_english(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    await setup_integration()
    prefix = f"component.{DOMAIN}.entity.sensor.order_total."

    da = await async_get_translations(hass, "da", "entity", [DOMAIN])
    en = await async_get_translations(hass, "en", "entity", [DOMAIN])

    assert da[f"{prefix}name"] == "Ordretotal"
    assert en[f"{prefix}name"] == "Order total"
    assert da[f"{prefix}state_attributes.difference.name"] == "Forskel fra bestilling"
    assert en[f"{prefix}state_attributes.difference.name"] == "Difference from ordered total"
    for attribute in ("ordered_total", "difference", *BREAKDOWN):
        for translations in (da, en):
            assert f"{prefix}state_attributes.{attribute}.name" in translations
