from datetime import date, datetime, time
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory

from bilkatogo import BilkaToGoClient, PickupSlots, SlotAvailability
from bilkatogo.const import BILKA_TIME_ZONE
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture

# The pickup capture was taken on Sunday 2026-11-08 in the evening.
PICKUP_CAPTURED = "2026-11-08T20:00:00+01:00"
# The Home Delivery capture (handle-mcp) was taken on Monday 2026-11-02.
HOME_CAPTURED = "2026-11-02T08:00:00+01:00"


async def fetch(bilka: FakeBilka, **kwargs: int) -> PickupSlots:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.get_pickup_slots(**kwargs)


@pytest.mark.asyncio
async def test_pickup_slots_for_the_carts_pickup_point_with_their_availability(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)

    slots = await fetch(logged_in_cart)

    [request] = logged_in_cart.calls("available_delivery_dates")
    assert dict(request.url.params) == {"u": "w"}
    assert slots.pickup_point is not None
    assert slots.pickup_point.name == "Bilka Vejle"
    assert slots.home_delivery is False
    monday = [
        (slot.slot.start, slot.slot.end, slot.availability, slot.order_before)
        for slot in slots.slots
        if slot.slot.date == date(2026, 11, 9)
    ]
    assert monday == [
        (time(8), time(10), SlotAvailability.SOLD_OUT, "i morgen kl. 4:00"),
        (time(10), time(12), SlotAvailability.OPEN, "i morgen kl. 6:00"),
        (time(12), time(14), SlotAvailability.OPEN, "i morgen kl. 8:00"),
        (time(14), time(16), SlotAvailability.OPEN, "i morgen kl. 10:00"),
        (time(16), time(18), SlotAvailability.OPEN, "i morgen kl. 12:00"),
        (time(18), time(22), SlotAvailability.OPEN, "i morgen kl. 14:00"),
    ]
    assert [slot.slot.date for slot in slots.slots].count(date(2026, 11, 10)) == 6


@pytest.mark.asyncio
async def test_home_delivery_night_slots_closed_past_their_deadline_and_open(
    home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(HOME_CAPTURED)

    slots = await fetch(home_delivery_cart)

    assert slots.home_delivery is True
    assert slots.pickup_point is None
    assert [(slot.slot.date, slot.availability, slot.order_before) for slot in slots.slots] == [
        (date(2026, 11, 2), SlotAvailability.CLOSED, "på søndag kl. 15:00"),
        (date(2026, 11, 4), SlotAvailability.OPEN, "i morgen kl. 15:00"),
    ]
    # "Levering vil ske natten til mandag": the night before the slot's date, until 06:00.
    night = slots.slots[0].slot
    assert night.starts_at == datetime(2026, 11, 2, 0, 0, tzinfo=BILKA_TIME_ZONE)
    assert night.ends_at == datetime(2026, 11, 2, 6, 0, tzinfo=BILKA_TIME_ZONE)


@pytest.mark.asyncio
async def test_days_without_slots_keep_bilkas_reason(
    home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(HOME_CAPTURED)

    slots = await fetch(home_delivery_cart)

    assert slots.days_without_slots == {date(2026, 11, 3): "udsolgt"}


@pytest.mark.asyncio
async def test_a_closed_day_without_a_reason_has_an_empty_one(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)

    slots = await fetch(logged_in_cart)

    # Sunday, today in the capture, has no intervals and no `closed_text`.
    assert slots.days_without_slots == {date(2026, 11, 8): ""}


@pytest.mark.asyncio
async def test_a_slots_note_is_bilkas_info_without_html(
    home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(HOME_CAPTURED)

    slots = await fetch(home_delivery_cart)

    assert slots.slots[0].note == (
        "Levering vil ske natten til mandag. Vi kommer med dine varer imellem 20:00 og 06:00. "
        "For at benytte det valgte tidsrum, skal du bestille senest på søndag kl. 15:00, "
        "bemærk at tidspunktet kan blive fuldt booket forinden."
    )


@pytest.mark.asyncio
async def test_a_slot_without_info_has_bilkas_closed_text_as_its_note(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    answer = load_fixture("available_delivery_dates_pickup.json")
    for interval in answer["availableIntervals"][1]["intervals"]:
        del interval["info"]
    logged_in_cart.script("available_delivery_dates", answer)

    slots = await fetch(logged_in_cart)

    assert [slot.note for slot in slots.slots[:2]] == ["udsolgt", None]


@pytest.mark.asyncio
async def test_the_slot_chosen_in_the_cart(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)

    slots = await fetch(logged_in_cart)

    assert slots.chosen is not None
    assert (slots.chosen.date, slots.chosen.start, slots.chosen.end) == (
        date(2026, 11, 11),
        time(14),
        time(16),
    )


@pytest.mark.asyncio
async def test_no_chosen_slot_without_a_delivery_date(
    home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(HOME_CAPTURED)

    slots = await fetch(home_delivery_cart)

    # The capture has `intervalStart` and `intervalEnd` but `deliveryDate: null`.
    assert slots.chosen is None


@pytest.mark.asyncio
async def test_a_chosen_slot_that_has_ended_is_none(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    # Like the Cart, Bilka keeps the last Order's slot for weeks after it ended.
    freezer.move_to("2026-11-11T16:00:01+01:00")

    slots = await fetch(logged_in_cart)

    assert slots.chosen is None


@pytest.mark.asyncio
async def test_next_open_is_the_earliest_open_slot(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)

    slots = await fetch(logged_in_cart)

    # 08:00 on Monday is sold out.
    assert slots.next_open is not None
    assert slots.next_open.slot.starts_at == datetime(2026, 11, 9, 10, tzinfo=BILKA_TIME_ZONE)


@pytest.mark.asyncio
async def test_no_next_open_when_every_slot_is_closed_or_sold_out(
    home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(HOME_CAPTURED)

    slots = await fetch(home_delivery_cart, days=2)

    assert [slot.availability for slot in slots.slots] == [SlotAvailability.CLOSED]
    assert slots.next_open is None


@pytest.mark.asyncio
async def test_days_counts_calendar_days_from_today_included(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)

    slots = await fetch(logged_in_cart, days=2)

    assert {slot.slot.date for slot in slots.slots} == {date(2026, 11, 9)}
    assert slots.days_without_slots == {date(2026, 11, 8): ""}


@pytest.mark.asyncio
async def test_today_is_the_day_in_copenhagen(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    # Still Sunday in UTC, already Monday in Copenhagen.
    freezer.move_to("2026-11-08T23:30:00+00:00")

    slots = await fetch(logged_in_cart, days=1)

    assert {slot.slot.date for slot in slots.slots} == {date(2026, 11, 9)}
    assert slots.days_without_slots == {}


@pytest.mark.asyncio
async def test_days_must_be_at_least_one(logged_in_cart: FakeBilka) -> None:
    with pytest.raises(ValueError, match="days"):
        await fetch(logged_in_cart, days=0)

    assert logged_in_cart.requests == []


def with_interval(start: str, end: str) -> dict[str, Any]:
    answer = load_fixture("available_delivery_dates_home.json")
    answer["availableIntervals"][2]["intervals"][0].update(intervalStart=start, intervalEnd=end)
    return answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end", "ends_at"),
    [
        ("20:00", "06:00", datetime(2026, 11, 5, 6, 0, tzinfo=BILKA_TIME_ZONE)),
        ("18:00", "00:00", datetime(2026, 11, 5, 0, 0, tzinfo=BILKA_TIME_ZONE)),
    ],
)
async def test_a_slot_that_ends_at_or_before_its_start_ends_the_next_day(
    home_delivery_cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
    start: str,
    end: str,
    ends_at: datetime,
) -> None:
    freezer.move_to(HOME_CAPTURED)
    home_delivery_cart.script("available_delivery_dates", with_interval(start, end))

    slots = await fetch(home_delivery_cart)

    assert slots.slots[-1].slot.ends_at == ends_at
