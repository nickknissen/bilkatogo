from datetime import timedelta
from typing import Any

import httpx
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import FakeBilka
from tests.ha.conftest import SetupIntegration, event_entity_id, fired

ORDER_NUMBER = "900000000001"

SUBSTITUTIONS = [
    {
        "original_product": "Æbler",
        "original_article_number": "1001",
        "original_size": "1 kg",
        "ordered_quantity": 4,
        "replacement_product": "Pærer",
        "replacement_article_number": "1002",
        "replacement_size": "750 g",
        "quantity": 4,
        "replacement_price": 4.0,
        "replacement_line_total": 12.0,
    },
    {
        "original_product": "Letmælk",
        "original_article_number": "1004",
        "original_size": "1 l",
        "ordered_quantity": 2,
        "replacement_product": "Minimælk",
        "replacement_article_number": "1005",
        "replacement_size": "0,5 l",
        "quantity": 3,
        "replacement_price": 6.5,
        "replacement_line_total": 19.5,
    },
]


def substitutions(hass: HomeAssistant, entry: MockConfigEntry) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_substitutions"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def set_status(bilka: FakeBilka, status_code: str) -> None:
    order = next(o for o in bilka.orders if o["orderId"] == ORDER_NUMBER)
    bilka.orders = [{**order, "status": status_code}]
    details = bilka.details[ORDER_NUMBER]
    bilka.details[ORDER_NUMBER] = {"order": {**details["order"], "status": status_code}}


async def next_update(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_substitutions_are_shown_once_the_order_is_packed(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    set_status(bilka, "Pakket")

    entry = await setup_integration()

    state = substitutions(hass, entry)
    assert state.state == "2"
    assert state.attributes["substitutions"] == SUBSTITUTIONS


async def test_details_are_fetched_only_while_packed_and_kept_after_pickup(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    set_status(bilka, "Ubehandlet")
    entry = await setup_integration()
    set_status(bilka, "Bestilt")
    await next_update(hass, freezer)
    assert substitutions(hass, entry).state == STATE_UNKNOWN
    assert bilka.calls("order_details") == []

    set_status(bilka, "Pakket")
    await next_update(hass, freezer)
    assert substitutions(hass, entry).state == "2"
    assert len(bilka.calls("order_details")) == 1

    set_status(bilka, "Faktureret")
    await next_update(hass, freezer)
    await next_update(hass, freezer)
    state = substitutions(hass, entry)
    assert state.state == "2"
    assert state.attributes["substitutions"] == SUBSTITUTIONS
    assert len(bilka.calls("order_details")) == 1


@pytest.mark.parametrize("status_code", ["Uafhentet", "Annulleret", "Fuldt udsolgt"])
async def test_details_are_fetched_for_orders_that_ended_without_pickup(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka, status_code: str
) -> None:
    set_status(bilka, status_code)

    entry = await setup_integration()

    assert substitutions(hass, entry).state == "2"


async def test_a_picked_up_order_fetches_its_details_once_after_a_restart(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    set_status(bilka, "Faktureret")

    entry = await setup_integration()

    assert substitutions(hass, entry).state == "2"
    pickup_code = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_pickup_code"
    )
    assert pickup_code is not None
    state = hass.states.get(pickup_code)
    assert state is not None
    assert state.state == "12345678"
    assert len(bilka.calls("order_details")) == 1

    await next_update(hass, freezer)
    await next_update(hass, freezer)
    assert substitutions(hass, entry).state == "2"
    assert len(bilka.calls("order_details")) == 1


async def test_each_substitution_is_announced_once_from_received_to_picked_up(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    set_status(bilka, "Bestilt")
    entry = await setup_integration()
    entity_id = event_entity_id(hass, entry, "substitution")
    assert fired(state_changes, entity_id) == []

    set_status(bilka, "Pakket")
    await next_update(hass, freezer)
    assert fired(state_changes, entity_id) == [
        {"event_type": "substitution", "order_number": ORDER_NUMBER, **substitution}
        for substitution in SUBSTITUTIONS
    ]
    last_fired = hass.states.get(entity_id)
    assert last_fired is not None

    await next_update(hass, freezer)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert substitutions(hass, entry).state == "2"

    set_status(bilka, "Faktureret")
    await next_update(hass, freezer)
    assert substitutions(hass, entry).state == "2"
    # Each update is an hour later, so firing again would change the timestamp.
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == last_fired.state


async def test_the_substitution_entity_fires_once_for_each_substitution(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    set_status(bilka, "Bestilt")
    entry = await setup_integration()

    set_status(bilka, "Pakket")
    await next_update(hass, freezer)
    await next_update(hass, freezer)

    assert fired(state_changes, event_entity_id(hass, entry, "substitution")) == [
        {"event_type": "substitution", "order_number": ORDER_NUMBER, **substitution}
        for substitution in SUBSTITUTIONS
    ]


async def test_the_substitution_entity_does_not_fire_again_after_a_failed_update(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    set_status(bilka, "Pakket")
    entry = await setup_integration()
    entity_id = event_entity_id(hass, entry, "substitution")
    before = hass.states.get(entity_id)
    assert before is not None

    bilka.script("order_history", httpx.ConnectError("down"))
    await next_update(hass, freezer)
    await next_update(hass, freezer)

    after = hass.states.get(entity_id)
    assert after is not None
    assert (after.state, after.attributes) == (before.state, before.attributes)


async def test_announced_substitutions_survive_a_restart(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    hass_storage[f"bilkatogo.{config_entry.entry_id}.substitutions"] = {
        "version": 1,
        "key": f"bilkatogo.{config_entry.entry_id}.substitutions",
        "data": {"announced": [{"order_number": ORDER_NUMBER, "original_article_number": "1001"}]},
    }
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    set_status(bilka, "Pakket")

    entry = await setup_integration()

    assert [
        event["original_product"]
        for event in fired(state_changes, event_entity_id(hass, entry, "substitution"))
    ] == ["Letmælk"]


async def test_substitutions_announced_by_name_are_not_announced_again(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """Up to 0.5.0, announced Substitutions were stored by the Original Product's name."""
    key = f"bilkatogo.{config_entry.entry_id}.substitutions"
    hass_storage[key] = {
        "version": 1,
        "key": key,
        "data": {
            "announced": [
                {"order_number": ORDER_NUMBER, "original_product": "Æbler"},
                {"order_number": "900000000002", "original_product": "Rugbrød"},
            ]
        },
    }
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    set_status(bilka, "Pakket")

    entry = await setup_integration()

    assert [
        event["original_product"]
        for event in fired(state_changes, event_entity_id(hass, entry, "substitution"))
    ] == ["Letmælk"]
    assert hass_storage[key]["data"]["announced"] == [
        {"order_number": ORDER_NUMBER, "original_article_number": "1001"},
        {"order_number": ORDER_NUMBER, "original_article_number": "1004"},
        {"order_number": "900000000002", "original_product": "Rugbrød"},
    ]


async def test_a_substitution_added_later_is_announced(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    set_status(bilka, "Pakket")
    entry = await setup_integration()
    state_changes = async_capture_events(hass, EVENT_STATE_CHANGED)

    details = bilka.details[ORDER_NUMBER]["order"]
    group = details["lines"][0]["lines"][0]
    substituted = next(line for line in group["orderlines"] if line.get("tagline") == "erstattet")
    # Another Product with the same name as an already substituted one.
    extra = {
        **substituted,
        "product": {**substituted["product"], "objectID": 1007},
        "children": [
            {**substituted["children"][0], "product": {"objectID": 1008, "name": "Mysli"}}
        ],
    }
    group["orderlines"] = [*group["orderlines"], extra]
    await next_update(hass, freezer)

    assert [
        (event["original_product"], event["original_article_number"])
        for event in fired(state_changes, event_entity_id(hass, entry, "substitution"))
    ] == [("Æbler", "1007")]
    announced = hass_storage[f"bilkatogo.{entry.entry_id}.substitutions"]["data"]["announced"]
    assert {"order_number": ORDER_NUMBER, "original_article_number": "1007"} in announced


async def test_a_new_current_order_drops_the_previous_substitutions(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    set_status(bilka, "Pakket")
    entry = await setup_integration()

    bilka.orders = [{**bilka.orders[0], "orderId": "900000000003", "status": "Bestilt"}]
    await next_update(hass, freezer)

    state = substitutions(hass, entry)
    assert state.state == STATE_UNKNOWN
    assert "substitutions" not in state.attributes


async def test_removing_the_entry_forgets_announced_substitutions(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    hass_storage: dict[str, Any],
) -> None:
    set_status(bilka, "Pakket")
    entry = await setup_integration()
    assert f"bilkatogo.{entry.entry_id}.substitutions" in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert f"bilkatogo.{entry.entry_id}.substitutions" not in hass_storage
