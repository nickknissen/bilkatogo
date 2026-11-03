from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import FakeBilka
from tests.ha.conftest import SetupIntegration

OCTOBER_TO_NOVEMBER = ("2026-10-01T00:00:00+01:00", "2026-12-01T00:00:00+01:00")


def calendar_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "calendar", DOMAIN, f"{entry.unique_id}_pickup_slots"
    )
    assert entity_id is not None
    return entity_id


def upcoming_order(bilka: FakeBilka, **changes: Any) -> dict[str, Any]:
    return {**bilka.orders[0], **changes}


def home_delivery(order: dict[str, Any]) -> dict[str, Any]:
    return {**order, "pickupzone": {**order["pickupzone"], "home_delivery": True}}


async def get_events(hass: HomeAssistant, entry: MockConfigEntry, start: str, end: str) -> Any:
    entity_id = calendar_id(hass, entry)
    response: Any = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": entity_id, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    return response[entity_id]["events"]


async def test_events_for_upcoming_and_past_orders(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    entry = await setup_integration()

    events = await get_events(hass, entry, *OCTOBER_TO_NOVEMBER)

    assert events == [
        {
            "start": "2026-10-27T12:00:00+01:00",
            "end": "2026-10-27T14:00:00+01:00",
            "summary": "BilkaToGo",
            "description": "#900000000001",
            "location": "Bilka Eksempelby",
        },
        {
            "start": "2026-11-03T16:00:00+01:00",
            "end": "2026-11-03T18:00:00+01:00",
            "summary": "BilkaToGo",
            "description": "#900000000002",
            "location": "Bilka Eksempelby",
        },
    ]


async def test_events_are_filtered_by_the_requested_range(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    entry = await setup_integration()

    events = await get_events(hass, entry, "2026-10-27T13:00:00+01:00", "2026-11-03T16:00:00+01:00")

    assert [event["description"] for event in events] == ["#900000000001"]


@pytest.mark.parametrize(
    "changes",
    [{"status": "Annulleret"}, {"status": "Fuldt udsolgt"}, {"deliveryDate": None}],
    ids=["cancelled", "sold_out", "no_pickup_slot"],
)
async def test_orders_are_left_out(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    changes: dict[str, Any],
) -> None:
    bilka.orders[0] = upcoming_order(bilka, **changes)
    entry = await setup_integration()

    events = await get_events(hass, entry, *OCTOBER_TO_NOVEMBER)

    assert [event["description"] for event in events] == ["#900000000001"]


async def test_home_delivery_has_no_location(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    bilka.orders = [home_delivery(upcoming_order(bilka))]
    entry = await setup_integration()

    [event] = await get_events(hass, entry, *OCTOBER_TO_NOVEMBER)

    assert event["summary"] == "BilkaToGo"
    assert "location" not in event


async def test_event_text_does_not_depend_on_the_language(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    hass.config.language = "da"
    bilka.orders = [upcoming_order(bilka)]
    entry = await setup_integration()

    [event] = await get_events(hass, entry, *OCTOBER_TO_NOVEMBER)

    assert (event["summary"], event["description"]) == ("BilkaToGo", "#900000000002")


async def test_no_pickup_code_in_an_event(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    order = upcoming_order(bilka, status="Pakket")
    bilka.orders = [
        {**order, "pickupzone": {**order["pickupzone"], "order_pickup_code": "55667788"}}
    ]
    bilka.details["900000000002"] = {
        "order": {**bilka.details["900000000001"]["order"], "orderId": "900000000002"}
    }
    entry = await setup_integration()

    [event] = await get_events(hass, entry, *OCTOBER_TO_NOVEMBER)

    assert entry.runtime_data.data.details.pickup_code.code == "12345678"
    assert not any(code in str(event) for code in ("55667788", "12345678"))


async def test_off_before_a_slot_with_the_next_event(
    hass: HomeAssistant, setup_integration: SetupIntegration, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-28T12:00:00+01:00")
    entry = await setup_integration()

    state = hass.states.get(calendar_id(hass, entry))

    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["message"] == "BilkaToGo"
    assert state.attributes["description"] == "#900000000002"


async def test_on_during_a_slot_with_the_current_event(
    hass: HomeAssistant, setup_integration: SetupIntegration, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-27T13:00:00+01:00")
    entry = await setup_integration()

    state = hass.states.get(calendar_id(hass, entry))

    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["description"] == "#900000000001"


async def test_off_with_the_next_event_once_a_slot_ends(
    hass: HomeAssistant, setup_integration: SetupIntegration, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-27T14:00:00+01:00")
    entry = await setup_integration()

    state = hass.states.get(calendar_id(hass, entry))

    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["description"] == "#900000000002"


async def test_off_without_an_event_after_the_last_slot(
    hass: HomeAssistant, setup_integration: SetupIntegration, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-04T12:00:00+01:00")
    entry = await setup_integration()

    state = hass.states.get(calendar_id(hass, entry))

    assert state is not None
    assert state.state == STATE_OFF
    assert "message" not in state.attributes
