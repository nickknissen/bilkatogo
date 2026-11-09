from datetime import datetime, timedelta

import httpx
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.bilkatogo.coordinator import backoff_interval
from tests.fake_bilka import FakeBilka, Scripted
from tests.ha.conftest import SetupIntegration

THREE_HOURS_BEFORE_SLOT = "2026-11-03 12:00:00+00:00"
DAY_BEFORE_SLOT = "2026-11-02 12:00:00+00:00"


@pytest.mark.parametrize(
    ("interval", "failures", "backed_off"),
    [
        (5, 0, 5),
        (5, 1, 10),
        (5, 2, 20),
        (5, 3, 40),
        (5, 4, 60),
        (5, 5, 60),
        (5, 12, 60),
        # A month-long outage at one poll an hour.
        (5, 720, 60),
        (60, 0, 60),
        (60, 1, 60),
        (60, 3, 60),
        (42, 1, 60),
    ],
)
def test_backoff_doubles_the_interval_per_failure_up_to_60_minutes(
    interval: int, failures: int, backed_off: int
) -> None:
    assert backoff_interval(timedelta(minutes=interval), failures) == timedelta(minutes=backed_off)


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
    freezer.move_to(DAY_BEFORE_SLOT)
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


async def next_poll_after(
    hass: HomeAssistant, bilka: FakeBilka, freezer: FrozenDateTimeFactory, minutes: int
) -> None:
    assert await polls_after(hass, bilka, freezer, minutes - 1) == 0
    assert await polls_after(hass, bilka, freezer, 1) == 1


FAILURES: dict[str, Scripted] = {
    "busy": httpx.Response(503, text="busy"),
    "rate_limited": httpx.Response(429, text="slow down"),
    "unreachable": httpx.ConnectError("down"),
}


@pytest.mark.parametrize("failure", FAILURES.values(), ids=FAILURES.keys())
async def test_backs_off_while_bilka_fails_in_the_fast_window_and_recovers_at_once(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
    failure: Scripted,
) -> None:
    freezer.move_to(THREE_HOURS_BEFORE_SLOT)
    await setup_integration()
    bilka.script("order_history", *[failure] * 5)

    await next_poll_after(hass, bilka, freezer, 5)
    await next_poll_after(hass, bilka, freezer, 10)
    await next_poll_after(hass, bilka, freezer, 20)
    await next_poll_after(hass, bilka, freezer, 40)
    await next_poll_after(hass, bilka, freezer, 60)
    # The fifth failure stays at 60 minutes; Bilka answers again after it.
    await next_poll_after(hass, bilka, freezer, 60)
    await next_poll_after(hass, bilka, freezer, 5)
    await next_poll_after(hass, bilka, freezer, 5)


async def test_a_failed_update_outside_the_fast_window_polls_again_after_60_minutes(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(DAY_BEFORE_SLOT)
    await setup_integration()
    bilka.script("order_history", FAILURES["busy"], FAILURES["busy"])

    await next_poll_after(hass, bilka, freezer, 60)
    await next_poll_after(hass, bilka, freezer, 60)
    await next_poll_after(hass, bilka, freezer, 60)


async def test_failing_pickup_slots_do_not_back_off(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(THREE_HOURS_BEFORE_SLOT)
    await setup_integration()
    bilka.script("available_delivery_dates", FAILURES["busy"], FAILURES["busy"])

    await next_poll_after(hass, bilka, freezer, 5)
    await next_poll_after(hass, bilka, freezer, 5)
    await next_poll_after(hass, bilka, freezer, 5)
