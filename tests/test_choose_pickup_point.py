from datetime import date, time
from decimal import Decimal

import httpx
import pytest
from freezegun.api import FrozenDateTimeFactory

from bilkatogo import (
    BilkaToGoClient,
    DeliveryChange,
    NoHomeAddressError,
    UnexpectedResponseError,
    UnknownPickupPointError,
)
from tests.conftest import MemoryTokenStorage
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture

# Before the Pickup Slot chosen in cart_logged_in.json, Sun 1/11 14:00-16:00.
BEFORE_THE_SLOT = "2026-10-30T12:00:00+01:00"


async def choose_point(bilka: FakeBilka, name: str, *then: str) -> DeliveryChange:
    """Move the Cart to `name`, then to each of `then`; the last move's change."""
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        change = await client.choose_pickup_point(name)
        for later in then:
            change = await client.choose_pickup_point(later)
    return change


@pytest.mark.asyncio
async def test_choosing_a_pickup_point_sends_its_id_and_zip_code_and_reports_the_change(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.unsold["1662"] = {"75191"}

    change = await choose_point(logged_in_cart, "Bilka Kolding")

    [request] = logged_in_cart.calls("set_delivery_address")
    assert dict(request.url.params) == {"u": "w", "id": "16230", "zipcode": "6000"}
    assert change.changed is True
    before, after = change.before, change.after
    assert before.pickup_point is not None and after.pickup_point is not None
    assert (before.pickup_point.name, after.pickup_point.name) == ("Bilka Vejle", "Bilka Kolding")
    assert (before.store_id, after.store_id) == ("1661", "1662")
    assert (before.total, after.total) == (Decimal("127.00"), Decimal("127.00"))
    assert (after.service_fee, after.delivery_fee, after.packaging) == (
        Decimal("39.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    )
    assert after.minimum_order is None
    assert [(line.product, line.unavailable_reason) for line in change.unavailable] == [
        ("Mini Mix", "Udsolgt")
    ]
    assert change.available_again == []
    assert change.gone == []


@pytest.mark.asyncio
async def test_moving_back_lists_the_line_as_available_again(logged_in_cart: FakeBilka) -> None:
    logged_in_cart.unsold["1662"] = {"75191"}

    change = await choose_point(logged_in_cart, "Bilka Kolding", "Bilka Vejle")

    assert [line.article_number for line in change.available_again] == ["75191"]
    assert change.unavailable == []
    assert change.after.lines[-1].available


@pytest.mark.asyncio
async def test_a_standalone_pickup_point_adds_a_delivery_fee_and_packaging(
    logged_in_cart: FakeBilka,
) -> None:
    change = await choose_point(logged_in_cart, "pickup fredericia")

    assert (change.before.delivery_fee, change.after.delivery_fee) == (
        Decimal("0.00"),
        Decimal("19.00"),
    )
    assert (change.before.packaging, change.after.packaging) == (Decimal("0.00"), Decimal("5.00"))
    assert change.after.total == Decimal("151.00")
    assert "Leveringsgebyr" not in {line.product for line in change.after.lines}


@pytest.mark.asyncio
async def test_a_chosen_pickup_slot_closed_at_the_new_place_is_reported(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(BEFORE_THE_SLOT)

    change = await choose_point(logged_in_cart, "Pickup Fredericia")

    kept = change.after.pickup_slot
    assert kept is not None
    assert (kept.date, kept.start, kept.end) == (date(2026, 11, 1), time(14), time(16))
    assert change.before.pickup_slot_closed is False
    assert change.pickup_slot_closed is True


@pytest.mark.asyncio
async def test_a_pickup_slot_that_stays_open_is_not_reported(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(BEFORE_THE_SLOT)
    logged_in_cart.slot_offered_at = {"16229", "16230"}

    change = await choose_point(logged_in_cart, "Bilka Kolding")

    assert change.after.pickup_slot is not None
    assert change.pickup_slot_closed is False


@pytest.mark.asyncio
async def test_an_unknown_name_raises_without_moving_the_cart(logged_in_cart: FakeBilka) -> None:
    with pytest.raises(UnknownPickupPointError, match="Bilka Atlantis"):
        await choose_point(logged_in_cart, "Bilka Atlantis")

    assert logged_in_cart.calls("set_delivery_address") == []


@pytest.mark.asyncio
async def test_the_home_address_is_not_a_pickup_point_to_choose(
    home_delivery_cart: FakeBilka,
) -> None:
    with pytest.raises(UnknownPickupPointError):
        await choose_point(home_delivery_cart, "Kunde Eksempel")

    assert home_delivery_cart.calls("set_delivery_address") == []


@pytest.mark.asyncio
async def test_a_place_bilka_does_not_know_raises_with_bilkas_message(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.script("set_delivery_address", load_fixture("set_delivery_address_unknown.json"))

    with pytest.raises(UnknownPickupPointError) as caught:
        await choose_point(logged_in_cart, "Bilka Kolding")

    assert caught.value.name == "Bilka Kolding"
    assert caught.value.reason == "Udleveringsstedet kunne ikke findes"
    assert logged_in_cart.cart_contents["deliveryAddress"]["name"] == "Bilka Vejle"


@pytest.mark.asyncio
async def test_another_refusal_is_not_taken_for_an_unknown_place(
    home_delivery_cart: FakeBilka,
) -> None:
    # Bilka's website names `eid` 428, BLOCKED_PRODUCTS, but it was never seen (#89).
    blocked = {"msg": "Varerne kan ikke leveres", "code": "BLOCKED_PRODUCTS", "eid": 428}
    async with (
        home_delivery_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        home_delivery_cart.script("set_delivery_address", blocked)
        with pytest.raises(UnexpectedResponseError, match="Varerne kan ikke leveres") as caught:
            await client.choose_pickup_point("Bilka Kolding")
        home_delivery_cart.cart_contents["deliveryAddress"] = load_fixture("cart.json")[
            "deliveryAddress"
        ]
        home_delivery_cart.script("set_delivery_address", blocked)
        with pytest.raises(UnexpectedResponseError, match="Varerne kan ikke leveres"):
            await client.choose_home_delivery()

    assert not isinstance(caught.value, UnknownPickupPointError)


@pytest.mark.asyncio
async def test_the_carts_own_pickup_point_is_reported_unchanged_without_sending_anything(
    logged_in_cart: FakeBilka,
) -> None:
    change = await choose_point(logged_in_cart, "Bilka Vejle")

    assert logged_in_cart.calls("set_delivery_address") == []
    assert change.changed is False
    assert change.before == change.after
    assert (change.unavailable, change.available_again, change.gone) == ([], [], [])


@pytest.mark.asyncio
async def test_an_answer_after_which_the_cart_did_not_move_raises(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.script("set_delivery_address", load_fixture("set_delivery_address.json"))

    with pytest.raises(UnexpectedResponseError, match="did not move"):
        await choose_point(logged_in_cart, "Bilka Kolding")


@pytest.mark.asyncio
async def test_an_answer_without_eid_minus_one_raises_with_bilkas_message(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.script("set_delivery_address", {"eid": 500, "msg": "Prøv igen senere"})

    with pytest.raises(UnexpectedResponseError, match="Prøv igen senere"):
        await choose_point(logged_in_cart, "Bilka Kolding")


@pytest.mark.asyncio
async def test_a_line_gone_after_the_move_is_reported(logged_in_cart: FakeBilka) -> None:
    def drop_mini_mix(request: httpx.Request) -> httpx.Response:
        # Never captured: Bilka keeps the lines it can't supply.
        response = logged_in_cart.set_delivery_address(request)
        logged_in_cart._set_line("75191", 0)
        return response

    logged_in_cart.script("set_delivery_address", drop_mini_mix)

    change = await choose_point(logged_in_cart, "Bilka Kolding")

    assert [line.article_number for line in change.gone] == ["75191"]


@pytest.mark.asyncio
async def test_home_delivery_without_a_known_home_address_raises_without_sending_anything(
    logged_in_cart: FakeBilka,
) -> None:
    async with (
        logged_in_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        with pytest.raises(NoHomeAddressError, match="once on bilkatogo.dk"):
            await client.choose_home_delivery()

    assert logged_in_cart.calls("set_delivery_address") == []


@pytest.mark.asyncio
async def test_home_delivery_goes_to_the_remembered_home_address(
    home_delivery_cart: FakeBilka, storage: MemoryTokenStorage
) -> None:
    async with (
        home_delivery_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await client.choose_pickup_point("Bilka Vejle")
    async with (
        home_delivery_cart.client() as http,
        BilkaToGoClient(EMAIL, token_storage=storage, httpx_client=http) as client,
    ):
        change = await client.choose_home_delivery()

    [_, request] = home_delivery_cart.calls("set_delivery_address")
    assert dict(request.url.params) == {"u": "w", "id": "185595", "zipcode": "9999"}
    assert change.changed is True
    assert (change.before.home_delivery, change.after.home_delivery) == (False, True)
    assert change.after.pickup_point is None
    assert change.after.store_id == "1661"
    assert change.after.minimum_order == Decimal("500.00")
    assert change.after.delivery_fee == Decimal("39.00")


@pytest.mark.asyncio
async def test_home_delivery_when_the_cart_is_on_it_sends_nothing(
    home_delivery_cart: FakeBilka,
) -> None:
    async with (
        home_delivery_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        change = await client.choose_home_delivery()

    assert home_delivery_cart.calls("set_delivery_address") == []
    assert change.changed is False
    assert change.after.home_delivery is True


@pytest.mark.asyncio
async def test_a_home_address_bilka_no_longer_knows_raises_with_bilkas_message(
    home_delivery_cart: FakeBilka,
) -> None:
    async with (
        home_delivery_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        await client.choose_pickup_point("Bilka Vejle")
        home_delivery_cart.script(
            "set_delivery_address", load_fixture("set_delivery_address_unknown.json")
        )
        with pytest.raises(NoHomeAddressError) as caught:
            await client.choose_home_delivery()

    assert caught.value.reason == "Udleveringsstedet kunne ikke findes"
    assert home_delivery_cart.cart_contents["deliveryAddress"]["name"] == "Bilka Vejle"
