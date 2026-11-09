"""Tests for the BilkaToGo sensor platform."""

from datetime import timedelta
from unittest.mock import patch

import httpx
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from bilkatogo import OrderStatus
from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import FakeBilka, load_fixture
from tests.ha.conftest import SNAPSHOT_TIME, SetupIntegration, pack_substituted_order


def order_status(hass: HomeAssistant, entry: MockConfigEntry) -> State:
    """Return the state of the Order Status sensor."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_order_status"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_all_entities(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test every sensor of a Packed Order with Substitutions."""
    freezer.move_to(SNAPSHOT_TIME)
    pack_substituted_order(bilka)
    with patch("custom_components.bilkatogo.PLATFORMS", [Platform.SENSOR]):
        entry = await setup_integration()

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


async def test_order_status_shows_the_current_order(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test the Order Status sensor shows the Current Order and lists every Order Status."""
    entry = await setup_integration()

    state = order_status(hass, entry)
    assert state.state == "received"
    assert state.attributes["status_code"] == "Bestilt"
    assert state.attributes["order_number"] == "900000000002"
    assert state.attributes["options"] == [
        "processing",
        "received",
        "packed",
        "picked_up",
        "not_collected",
        "cancelled",
        "sold_out",
        "unrecognized",
    ]


async def test_order_status_follows_bilka_every_60_minutes(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the Order Status sensor picks up a new Order Status after 60 minutes."""
    freezer.move_to("2026-11-02 12:00:00+00:00")
    entry = await setup_integration()
    bilka.orders[0] = {**bilka.orders[0], "status": "Pakket"}

    freezer.tick(timedelta(minutes=59))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert order_status(hass, entry).state == "received"

    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = order_status(hass, entry)
    assert state.state == "packed"
    assert state.attributes["status_code"] == "Pakket"


async def test_unrecognized_bilka_status_code_differs_from_no_order(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    """Test an unknown Bilka Status Code shows as unrecognized, not as unknown."""
    bilka.orders[0] = {**bilka.orders[0], "status": "Noget nyt"}

    entry = await setup_integration()

    state = order_status(hass, entry)
    assert state.state == "unrecognized"
    assert state.attributes["status_code"] == "Noget nyt"


async def test_order_status_is_unknown_without_orders(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    """Test the Order Status sensor is unknown, without attributes, when there is no Order."""
    bilka.orders = []

    entry = await setup_integration()

    state = order_status(hass, entry)
    assert state.state == STATE_UNKNOWN
    assert "status_code" not in state.attributes
    assert "order_number" not in state.attributes


async def test_order_status_attributes_are_translated_in_danish_and_english(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test the Order Status sensor's attribute names have Danish and English translations."""
    await setup_integration()
    prefix = f"component.{DOMAIN}.entity.sensor.order_status.state_attributes."

    da = await async_get_translations(hass, "da", "entity", [DOMAIN])
    en = await async_get_translations(hass, "en", "entity", [DOMAIN])

    assert da[f"{prefix}order_number.name"] == "Ordrenummer"
    assert en[f"{prefix}order_number.name"] == "Order number"


async def test_order_status_states_are_translated_in_danish_and_english(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test every Order Status has a Danish and an English translation."""
    await setup_integration()
    prefix = f"component.{DOMAIN}.entity.sensor.order_status.state."

    da = await async_get_translations(hass, "da", "entity", [DOMAIN])
    en = await async_get_translations(hass, "en", "entity", [DOMAIN])

    assert da[f"{prefix}packed"] == "Pakket"
    assert da[f"{prefix}received"] == "Modtaget"
    assert en[f"{prefix}picked_up"] == "Picked up"
    assert da[f"{prefix}unrecognized"] == "Ukendt"
    assert en[f"{prefix}unrecognized"] == "Unknown"
    for translations in (da, en):
        assert {key.removeprefix(prefix) for key in translations if key.startswith(prefix)} == {
            status.value for status in OrderStatus if status is not OrderStatus.UNKNOWN
        } | {"unrecognized"}


def sensor_state(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> State:
    """Return the state of the sensor with the given key."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_{key}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_pickup_slot_start_is_a_copenhagen_timestamp(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test the Pickup Slot sensor reads the slot in Copenhagen time in any time zone."""
    await hass.config.async_set_time_zone("America/New_York")

    entry = await setup_integration()

    state = sensor_state(hass, entry, "pickup_slot")
    assert state.state == "2026-11-03T15:00:00+00:00"
    assert state.attributes["device_class"] == "timestamp"
    assert state.attributes["pickup_slot_end"] == "2026-11-03T18:00:00+01:00"


async def test_pickup_point_shows_the_current_orders_store(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test the Pickup Point sensor shows where the Current Order is collected."""
    entry = await setup_integration()

    state = sensor_state(hass, entry, "pickup_point")
    assert state.state == "Bilka Eksempelby"


async def test_order_number_and_order_total_are_not_repeated(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test no other sensor repeats the Order Number or Order Total as an attribute."""
    entry = await setup_integration()

    for key in ("order_total", "pickup_slot", "pickup_point", "pickup_code"):
        attributes = sensor_state(hass, entry, key).attributes
        assert "order_number" not in attributes
        assert "order_total" not in attributes


async def test_pickup_code_shows_once_the_current_order_is_packed(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the Pickup Code is unknown and not fetched until the Current Order is Packed."""
    freezer.move_to("2026-11-02 12:00:00+00:00")
    entry = await setup_integration()
    assert sensor_state(hass, entry, "pickup_code").state == STATE_UNKNOWN
    assert bilka.calls("order_details") == []

    bilka.orders[0] = {**bilka.orders[0], "status": "Pakket"}
    freezer.tick(timedelta(minutes=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert sensor_state(hass, entry, "pickup_code").state == "87654321"


async def test_pickup_sensors_are_unknown_without_orders(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    """Test the Pickup Slot, Pickup Code and Pickup Point sensors are unknown without an Order."""
    bilka.orders = []

    entry = await setup_integration()

    for key in ("pickup_slot", "pickup_code", "pickup_point"):
        state = sensor_state(hass, entry, key)
        assert state.state == STATE_UNKNOWN
        assert "order_number" not in state.attributes
        assert "pickup_slot_end" not in state.attributes


async def test_pickup_sensor_names_are_translated_in_danish_and_english(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test the Pickup Slot, Pickup Code and Pickup Point sensors have Danish and English names."""
    await setup_integration()
    prefix = f"component.{DOMAIN}.entity.sensor."

    da = await async_get_translations(hass, "da", "entity", [DOMAIN])
    en = await async_get_translations(hass, "en", "entity", [DOMAIN])

    assert da[f"{prefix}pickup_slot.name"] == "Afhentningstidspunkt"
    assert da[f"{prefix}pickup_code.name"] == "Afhentningskode"
    assert da[f"{prefix}pickup_point.name"] == "Afhentningssted"
    assert en[f"{prefix}pickup_slot.name"] == "Pickup slot"
    assert en[f"{prefix}pickup_code.name"] == "Pickup code"
    assert en[f"{prefix}pickup_point.name"] == "Pickup point"


# When the pickup capture was taken, and the Home Delivery one (handle-mcp).
PICKUP_CAPTURED = "2026-11-08T20:00:00+01:00"
HOME_CAPTURED = "2026-11-02T08:00:00+01:00"


async def test_next_pickup_slot_is_the_earliest_open_slot_at_the_carts_pickup_point(
    hass: HomeAssistant, setup_integration: SetupIntegration, freezer: FrozenDateTimeFactory
) -> None:
    """Test the next Pickup Slot sensor shows the earliest open slot at the Cart's Pickup Point."""
    freezer.move_to(PICKUP_CAPTURED)

    entry = await setup_integration()

    state = sensor_state(hass, entry, "next_pickup_slot")
    # Monday 08:00 is sold out.
    assert state.state == "2026-11-09T09:00:00+00:00"
    assert state.attributes["device_class"] == "timestamp"
    assert {
        key: state.attributes[key]
        for key in ("end", "availability", "order_before", "pickup_point", "open_slots")
    } == {
        "end": "2026-11-09T12:00:00+01:00",
        "availability": "open",
        "order_before": "i morgen kl. 6:00",
        "pickup_point": "Bilka Waves, Hundige",
        "open_slots": 11,
    }
    assert state.attributes["note"].startswith("For at benytte det valgte tidsrum")


async def test_next_pickup_slot_for_home_delivery_has_no_pickup_point(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    home_delivery_cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the next Pickup Slot for Home Delivery has no Pickup Point or Home Address."""
    freezer.move_to(HOME_CAPTURED)

    entry = await setup_integration()

    state = sensor_state(hass, entry, "next_pickup_slot")
    assert state.state == "2026-11-03T23:00:00+00:00"
    assert state.attributes["end"] == "2026-11-04T06:00:00+01:00"
    assert state.attributes["open_slots"] == 1
    assert "pickup_point" not in state.attributes
    for value in state.attributes.values():
        assert "Eksempel" not in str(value)


async def test_next_pickup_slot_is_unknown_without_an_open_slot(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the next Pickup Slot sensor is unknown when every Pickup Slot is sold out."""
    freezer.move_to(PICKUP_CAPTURED)
    answer = load_fixture("available_delivery_dates_pickup.json")
    for day in answer["availableIntervals"]:
        for interval in day.get("intervals") or []:
            interval.update(availability_bit=4, closed=1, closed_text="udsolgt")
    bilka.script("available_delivery_dates", answer)

    entry = await setup_integration()

    state = sensor_state(hass, entry, "next_pickup_slot")
    assert state.state == STATE_UNKNOWN
    assert state.attributes["open_slots"] == 0
    assert "end" not in state.attributes


async def test_next_pickup_slot_is_unavailable_when_bilka_fails_it_alone(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test only the next Pickup Slot sensor is unavailable while Bilka fails to list slots.

    The other sensors keep their state, and the next Pickup Slot returns at the next update.
    """
    freezer.move_to(PICKUP_CAPTURED)
    bilka.script("available_delivery_dates", httpx.Response(500, text="oops"))

    entry = await setup_integration()

    assert sensor_state(hass, entry, "next_pickup_slot").state == STATE_UNAVAILABLE
    assert sensor_state(hass, entry, "order_status").state == "received"
    assert sensor_state(hass, entry, "pickup_point").state == "Bilka Eksempelby"

    freezer.tick(timedelta(minutes=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert sensor_state(hass, entry, "next_pickup_slot").state == "2026-11-09T09:00:00+00:00"


async def test_next_pickup_slot_is_translated_in_danish_and_english(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test the next Pickup Slot sensor and its attributes have Danish and English names."""
    await setup_integration()
    prefix = f"component.{DOMAIN}.entity.sensor.next_pickup_slot."

    da = await async_get_translations(hass, "da", "entity", [DOMAIN])
    en = await async_get_translations(hass, "en", "entity", [DOMAIN])

    assert da[f"{prefix}name"] == "Næste ledige afhentningstidspunkt"
    assert en[f"{prefix}name"] == "Next available pickup slot"
    attributes = {"end", "availability", "order_before", "note", "pickup_point", "open_slots"}
    for translations in (da, en):
        named = {
            key.removeprefix(f"{prefix}state_attributes.").removesuffix(".name")
            for key in translations
            if key.startswith(f"{prefix}state_attributes.") and key.endswith(".name")
        }
        assert named == attributes
