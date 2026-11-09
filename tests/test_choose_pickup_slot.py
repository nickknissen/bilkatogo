import copy
from datetime import date, time
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory

from bilkatogo import (
    BilkaToGoClient,
    ChosenPickupSlot,
    PickupSlot,
    PickupSlotUnavailableError,
    SlotAvailability,
    UnexpectedResponseError,
)
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture

# The pickup slots were captured on Sunday 2026-11-08 in the evening.
PICKUP_CAPTURED = "2026-11-08T20:00:00+01:00"
# The Home Delivery slots (handle-mcp) were captured on Monday 2026-11-02.
HOME_CAPTURED = "2026-11-02T08:00:00+01:00"


async def choose(bilka: FakeBilka, slot: PickupSlot) -> ChosenPickupSlot:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.choose_pickup_slot(slot)


@pytest.mark.asyncio
async def test_an_open_slot_is_chosen_and_the_replaced_one_returned(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    logged_in_cart.cart_contents["deliveryDate"].update(deliveryDate="2026-11-11")

    result = await choose(logged_in_cart, PickupSlot(date(2026, 11, 9), time(16), time(18)))

    [request] = logged_in_cart.calls("set_delivery_date")
    assert dict(request.url.params) == {
        "u": "w",
        "deliveryDate": "2026-11-09",
        "intervalStart": "16:00",
        "intervalEnd": "18:00",
    }
    assert (result.chosen.date, result.chosen.start, result.chosen.end) == (
        date(2026, 11, 9),
        time(16),
        time(18),
    )
    assert result.replaced is not None
    assert (result.replaced.date, result.replaced.start, result.replaced.end) == (
        date(2026, 11, 11),
        time(14),
        time(16),
    )
    assert result.changed is True
    assert logged_in_cart.cart_contents["deliveryDate"]["deliveryDate"] == "2026-11-09"


@pytest.mark.asyncio
async def test_the_already_chosen_slot_is_returned_without_sending_anything(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    logged_in_cart.cart_contents["deliveryDate"].update(
        deliveryDate="2026-11-10", intervalStart="16:00", intervalEnd="18:00"
    )
    slot = PickupSlot(date(2026, 11, 10), time(16), time(18))

    result = await choose(logged_in_cart, slot)

    assert logged_in_cart.calls("set_delivery_date") == []
    assert result.changed is False
    assert (result.chosen.date, result.chosen.start, result.chosen.end) == (
        date(2026, 11, 10),
        time(16),
        time(18),
    )
    assert result.replaced is not None
    assert str(result.replaced) == "2026-11-10 16:00-18:00"


@pytest.mark.asyncio
async def test_the_already_chosen_slot_is_returned_even_when_it_has_sold_out(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    # Monday 08:00-10:00 is sold out in the capture.
    logged_in_cart.cart_contents["deliveryDate"].update(
        deliveryDate="2026-11-09", intervalStart="08:00", intervalEnd="10:00"
    )

    result = await choose(logged_in_cart, PickupSlot(date(2026, 11, 9), time(8), time(10)))

    assert logged_in_cart.calls("set_delivery_date") == []
    assert result.changed is False
    assert str(result.chosen) == "2026-11-09 08:00-10:00"


@pytest.mark.asyncio
async def test_the_home_delivery_night_slot_is_sent_as_listed(
    home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(HOME_CAPTURED)

    result = await choose(home_delivery_cart, PickupSlot(date(2026, 11, 4), time(0), time(6)))

    [request] = home_delivery_cart.calls("set_delivery_date")
    assert (
        request.url.params["deliveryDate"],
        request.url.params["intervalStart"],
        request.url.params["intervalEnd"],
    ) == ("2026-11-04", "00:00", "06:00")
    assert str(result.chosen) == "2026-11-04 00:00-06:00"
    assert str(result.replaced) == "2026-11-11 15:00-22:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cart_fixture", "captured", "slot", "availability", "reason"),
    [
        (
            "logged_in_cart",
            PICKUP_CAPTURED,
            PickupSlot(date(2026, 11, 9), time(8), time(10)),
            SlotAvailability.SOLD_OUT,
            "it is sold out",
        ),
        (
            "home_delivery_cart",
            HOME_CAPTURED,
            PickupSlot(date(2026, 11, 2), time(0), time(6)),
            SlotAvailability.CLOSED,
            "it is closed",
        ),
        (
            "logged_in_cart",
            PICKUP_CAPTURED,
            PickupSlot(date(2026, 11, 9), time(9), time(11)),
            None,
            "Bilka does not offer it for the Cart in the next 14 days",
        ),
    ],
)
async def test_a_slot_that_is_not_open_raises_without_choosing_it(
    request: pytest.FixtureRequest,
    freezer: FrozenDateTimeFactory,
    cart_fixture: str,
    captured: str,
    slot: PickupSlot,
    availability: SlotAvailability | None,
    reason: str,
) -> None:
    bilka: FakeBilka = request.getfixturevalue(cart_fixture)
    freezer.move_to(captured)
    before = copy.deepcopy(bilka.cart_contents["deliveryDate"])

    with pytest.raises(PickupSlotUnavailableError) as unavailable:
        await choose(bilka, slot)

    assert bilka.calls("set_delivery_date") == []
    assert unavailable.value.slot == slot
    assert unavailable.value.availability == availability
    assert unavailable.value.reason == reason
    assert unavailable.value.refused is False
    assert bilka.cart_contents["deliveryDate"] == before


@pytest.mark.asyncio
async def test_a_slot_that_sold_out_in_between_carries_bilkas_refusal(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    logged_in_cart.script("set_delivery_date", load_fixture("set_delivery_date_unavailable.json"))
    slot = PickupSlot(date(2026, 11, 9), time(16), time(18))

    with pytest.raises(PickupSlotUnavailableError) as refused:
        await choose(logged_in_cart, slot)

    assert len(logged_in_cart.calls("set_delivery_date")) == 1
    assert refused.value.slot == slot
    assert refused.value.reason == "Det valgte tidspunkt er ikke tilgængelig"
    assert refused.value.availability is None
    assert refused.value.refused is True
    assert str(refused.value) == (
        "The Pickup Slot 2026-11-09 16:00-18:00 cannot be chosen: "
        "Det valgte tidspunkt er ikke tilgængelig"
    )


@pytest.mark.asyncio
async def test_an_eid_406_without_a_code_is_also_a_refusal(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    refusal = load_fixture("set_delivery_date_unavailable.json")
    del refusal["code"]
    logged_in_cart.script("set_delivery_date", refusal)

    with pytest.raises(PickupSlotUnavailableError, match="ikke tilgængelig"):
        await choose(logged_in_cart, PickupSlot(date(2026, 11, 9), time(16), time(18)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"deliveryDate": "2026-11-10"},
            "SetDeliveryDate answered with 2026-11-10 16:00-18:00, not 2026-11-09 16:00-18:00",
        ),
        (
            {"intervalEnd": "20:00"},
            "SetDeliveryDate answered with 2026-11-09 16:00-20:00, not 2026-11-09 16:00-18:00",
        ),
        (
            {"intervalStart": None},
            "SetDeliveryDate answered without a Pickup Slot",
        ),
        ({"eid": 0}, "SetDeliveryDate answered without eid -1"),
    ],
)
async def test_an_answer_that_does_not_confirm_the_slot_raises(
    logged_in_cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
    change: dict[str, Any],
    message: str,
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    answer = {
        **load_fixture("set_delivery_date.json"),
        "deliveryDate": "2026-11-09",
        "intervalStart": "16:00",
        "intervalEnd": "18:00",
        **change,
    }
    logged_in_cart.script("set_delivery_date", answer)

    with pytest.raises(UnexpectedResponseError) as unexpected:
        await choose(logged_in_cart, PickupSlot(date(2026, 11, 9), time(16), time(18)))

    assert str(unexpected.value) == message


@pytest.mark.asyncio
async def test_bilkas_times_are_compared_as_times(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    logged_in_cart.script(
        "set_delivery_date",
        {
            **load_fixture("set_delivery_date.json"),
            "deliveryDate": "2026-11-09",
            "intervalStart": "16:00:00",
            "intervalEnd": "18:00:00",
        },
    )

    result = await choose(logged_in_cart, PickupSlot(date(2026, 11, 9), time(16), time(18)))

    assert (result.chosen.start, result.chosen.end) == (time(16), time(18))
