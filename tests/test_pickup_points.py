import pytest

from bilkatogo import BilkaToGoClient, BilkaToGoError, UnknownPickupPointError
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka


@pytest.mark.asyncio
async def test_pickup_points_have_name_address_and_store(bilka: FakeBilka) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        points = await client.get_pickup_points()

    [request] = bilka.calls("available_delivery_addresses")
    assert request.method == "GET"
    assert dict(request.url.params) == {"u": "w", "orderByZipcode": "1"}

    assert len(points) == 22
    by_name = {point.name: point for point in points}
    fredericia = by_name["Pickup Fredericia"]
    assert fredericia.address == "Strandvejen 220"
    assert fredericia.zip_code == "7000"
    assert fredericia.city == "Fredericia"
    assert fredericia.store_id == "1662"
    assert by_name["Bilka Kolding"].store_id == "1662"
    assert by_name["PickUp Skejby"].store_id == "1651"
    assert by_name["Bilka Tilst"].store_id == "1651"
    assert by_name["Bilka Vejle"].store_id == "1661"
    assert by_name["Bilka Odense"].address == "Niels Bohrs Alle 150"


@pytest.mark.asyncio
async def test_cart_pickup_point_is_the_customers_chosen_one(bilka: FakeBilka) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        point = await client.get_cart_pickup_point()

    [request] = bilka.calls("cart")
    assert request.method == "GET"
    assert dict(request.url.params) == {"u": "w", "extra": "deliveryAddress,deliveryDate"}

    assert point is not None
    assert point.name == "Bilka Waves, Hundige"
    assert point.address == "Over Bølgen 1"
    assert point.store_id == "1653"


@pytest.mark.asyncio
async def test_cart_pickup_point_survives_a_cart_line_that_does_not_parse(
    bilka: FakeBilka,
) -> None:
    bilka.cart_contents["lines"].append(
        {"headline": "Andet", "lines": [{"orderlines": [{"quantity": "not a number"}]}]}
    )
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        point = await client.get_cart_pickup_point()

    assert point is not None
    assert point.name == "Bilka Waves, Hundige"


@pytest.mark.asyncio
async def test_home_delivery_cart_has_no_pickup_point(home_delivery_cart: FakeBilka) -> None:
    async with (
        home_delivery_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        assert await client.get_cart_pickup_point() is None


@pytest.mark.asyncio
async def test_cart_without_a_pickup_point_has_none(bilka: FakeBilka) -> None:
    bilka.cart_contents = {**bilka.cart_contents, "deliveryAddress": None}
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        assert await client.get_cart_pickup_point() is None


@pytest.mark.asyncio
async def test_pickup_point_by_name_ignores_case_and_gives_its_store(bilka: FakeBilka) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        point = await client.get_pickup_point("pickup fredericia")

    assert point.name == "Pickup Fredericia"
    assert point.store_id == "1662"


@pytest.mark.asyncio
async def test_unknown_pickup_point_name_raises_naming_it(bilka: FakeBilka) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        with pytest.raises(UnknownPickupPointError, match="Bilka Atlantis") as caught:
            await client.get_pickup_point("Bilka Atlantis")

    assert isinstance(caught.value, BilkaToGoError)
    assert caught.value.name == "Bilka Atlantis"


@pytest.mark.asyncio
async def test_pickup_points_leave_out_the_home_address_while_the_cart_is_on_home_delivery(
    home_delivery_cart: FakeBilka,
) -> None:
    async with (
        home_delivery_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        points = await client.get_pickup_points()

    assert len(points) == 22
    assert "185595" not in {point.id for point in points}
    assert "Kunde Eksempel" not in {point.name for point in points}


@pytest.mark.asyncio
async def test_pickup_points_have_bilkas_id(bilka: FakeBilka) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        point = await client.get_pickup_point("Bilka Kolding")

    assert point.id == "16230"
