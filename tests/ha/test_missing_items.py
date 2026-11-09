"""Tests for announcing Missing Items."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import FakeBilka
from tests.ha.conftest import SetupIntegration, event_entity_id, fired

ORDER_NUMBER = "900000000003"

MISSING_ITEMS = [
    {"product": "Skyr", "article_number": "932058", "ordered_quantity": 4, "missing_quantity": 2},
    {
        "product": "Havregryn",
        "article_number": "1006",
        "ordered_quantity": 1,
        "missing_quantity": 1,
    },
]


def missing_items(hass: HomeAssistant, entry: MockConfigEntry) -> State:
    """Return the state of the Missing Items sensor."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_missing_items"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def set_status(bilka: FakeBilka, status_code: str) -> None:
    """Make the Order with Missing Items the only Order, with the given Bilka Status Code."""
    bilka.orders = [{**bilka.orders[0], "orderId": ORDER_NUMBER, "status": status_code}]
    details = bilka.details[ORDER_NUMBER]
    bilka.details[ORDER_NUMBER] = {"order": {**details["order"], "status": status_code}}


async def next_update(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move time on an hour and let the next update run."""
    freezer.tick(timedelta(minutes=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_missing_items_are_shown_once_the_order_is_packed(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    """Test the Missing Items sensor counts and lists the Missing Items of a Packed Order."""
    set_status(bilka, "Pakket")

    entry = await setup_integration()

    state = missing_items(hass, entry)
    assert state.state == "2"
    assert state.attributes["missing_items"] == MISSING_ITEMS


async def test_each_missing_item_is_announced_once_from_received_to_picked_up(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test each Missing Item is announced once, and not again on reload or when Picked Up."""
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    set_status(bilka, "Bestilt")
    entry = await setup_integration()
    entity_id = event_entity_id(hass, entry, "missing_item")
    assert fired(state_changes, entity_id) == []

    set_status(bilka, "Pakket")
    await next_update(hass, freezer)
    assert fired(state_changes, entity_id) == [
        {"event_type": "missing_item", "order_number": ORDER_NUMBER, **item}
        for item in MISSING_ITEMS
    ]
    last_fired = hass.states.get(entity_id)
    assert last_fired is not None

    await next_update(hass, freezer)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert missing_items(hass, entry).state == "2"

    set_status(bilka, "Faktureret")
    await next_update(hass, freezer)
    assert missing_items(hass, entry).state == "2"
    # Each update is an hour later, so firing again would change the timestamp.
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == last_fired.state


async def test_the_missing_item_entity_fires_for_missing_items_found_at_setup(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    """Test the Missing Item event entity fires for the Missing Items already there at setup."""
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    set_status(bilka, "Pakket")

    entry = await setup_integration()

    assert fired(state_changes, event_entity_id(hass, entry, "missing_item")) == [
        {"event_type": "missing_item", "order_number": ORDER_NUMBER, **item}
        for item in MISSING_ITEMS
    ]


async def test_event_entities_are_translated_in_danish_and_english(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    """Test the event entities, their events and attributes have Danish and English names."""
    await setup_integration()
    prefix = f"component.{DOMAIN}.entity.event."

    da = await async_get_translations(hass, "da", "entity", [DOMAIN])
    en = await async_get_translations(hass, "en", "entity", [DOMAIN])

    assert da[f"{prefix}substitution.name"] == "Erstatningsvare"
    assert da[f"{prefix}missing_item.name"] == "Manglende vare"
    assert en[f"{prefix}substitution.name"] == "Substitution"
    assert en[f"{prefix}missing_item.name"] == "Missing item"
    for key, attributes in (
        (
            "substitution",
            (
                "order_number",
                "original_product",
                "original_article_number",
                "ordered_quantity",
                "replacement_product",
                "replacement_article_number",
                "quantity",
            ),
        ),
        ("missing_item", ("order_number", *MISSING_ITEMS[0])),
    ):
        for translations in (da, en):
            assert f"{prefix}{key}.state_attributes.event_type.state.{key}" in translations
            for attribute in attributes:
                assert f"{prefix}{key}.state_attributes.{attribute}.name" in translations


async def test_announced_missing_items_survive_a_restart(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """Test Missing Items announced before a restart are not announced again."""
    hass_storage[f"bilkatogo.{config_entry.entry_id}.missing_items"] = {
        "version": 1,
        "key": f"bilkatogo.{config_entry.entry_id}.missing_items",
        "data": {"announced": [{"order_number": ORDER_NUMBER, "article_number": "932058"}]},
    }
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    set_status(bilka, "Pakket")

    entry = await setup_integration()

    assert [
        event["product"]
        for event in fired(state_changes, event_entity_id(hass, entry, "missing_item"))
    ] == ["Havregryn"]


async def test_removing_the_entry_forgets_announced_missing_items(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    hass_storage: dict[str, Any],
) -> None:
    """Test removing the config entry deletes the stored announced Missing Items."""
    set_status(bilka, "Pakket")
    entry = await setup_integration()
    assert f"bilkatogo.{entry.entry_id}.missing_items" in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert f"bilkatogo.{entry.entry_id}.missing_items" not in hass_storage
