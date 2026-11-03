from datetime import datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from tests.fake_bilka import FakeBilka
from tests.ha.conftest import SetupIntegration

THREE_HOURS_BEFORE_SLOT = "2026-11-03 12:00:00+00:00"


async def polls_after(
    hass: HomeAssistant, bilka: FakeBilka, freezer: FrozenDateTimeFactory, minutes: int
) -> int:
    before = len(bilka.calls("order_history"))
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    return len(bilka.calls("order_history")) - before


async def test_polls_every_5_minutes_from_3_hours_before_the_pickup_slot(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(THREE_HOURS_BEFORE_SLOT)
    await setup_integration()

    assert await polls_after(hass, bilka, freezer, 5) == 1
    assert await polls_after(hass, bilka, freezer, 5) == 1


async def test_polls_every_60_minutes_when_no_pickup_slot_starts_within_3_hours(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-11-02 12:00:00+00:00")
    await setup_integration()

    assert await polls_after(hass, bilka, freezer, 59) == 0
    assert await polls_after(hass, bilka, freezer, 1) == 1


async def test_switches_to_5_minutes_exactly_3_hours_before_the_pickup_slot(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-11-03 11:30:00+00:00")
    await setup_integration()

    assert await polls_after(hass, bilka, freezer, 29) == 0
    assert await polls_after(hass, bilka, freezer, 1) == 1
    assert await polls_after(hass, bilka, freezer, 5) == 1


@pytest.mark.parametrize(
    ("delivery_date", "start", "three_hours_before"),
    [
        # 05:00 summer time is 03:00 UTC; the clocks went forward at 01:00 UTC.
        ("2027-03-28", "05:00:00", "2027-03-28 00:00:00+00:00"),
        # 04:00 winter time is 03:00 UTC; the clocks went back at 01:00 UTC.
        ("2027-10-31", "04:00:00", "2027-10-31 00:00:00+00:00"),
    ],
    ids=["clocks_go_forward", "clocks_go_back"],
)
async def test_the_3_hours_before_the_pickup_slot_are_real_hours_when_the_clocks_change(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
    delivery_date: str,
    start: str,
    three_hours_before: str,
) -> None:
    bilka.orders[0] = {
        **bilka.orders[0],
        "deliveryDate": delivery_date,
        "deliveryInterval": {"start": start, "end": "07:00:00"},
    }
    freezer.move_to(datetime.fromisoformat(three_hours_before) - timedelta(minutes=30))
    await setup_integration()

    assert await polls_after(hass, bilka, freezer, 29) == 0
    assert await polls_after(hass, bilka, freezer, 1) == 1
    assert await polls_after(hass, bilka, freezer, 5) == 1


async def test_keeps_polling_every_5_minutes_after_the_slot_while_unfinished(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-11-03 20:00:00+00:00")
    await setup_integration()

    assert await polls_after(hass, bilka, freezer, 5) == 1


@pytest.mark.parametrize("status_code", ["Faktureret", "Uafhentet", "Annulleret", "Fuldt udsolgt"])
async def test_returns_to_60_minutes_once_the_current_order_is_finished(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
    status_code: str,
) -> None:
    freezer.move_to(THREE_HOURS_BEFORE_SLOT)
    await setup_integration()
    bilka.orders[0] = {**bilka.orders[0], "status": status_code}

    assert await polls_after(hass, bilka, freezer, 5) == 1
    assert await polls_after(hass, bilka, freezer, 59) == 0
    assert await polls_after(hass, bilka, freezer, 1) == 1
