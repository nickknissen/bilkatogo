from decimal import Decimal

import pytest

from bilkatogo import BilkaToGoClient, HomeAddress
from tests.conftest import MemoryTokenStorage
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture


def client(bilka: FakeBilka, http, storage: MemoryTokenStorage | None = None) -> BilkaToGoClient:
    return BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http)


@pytest.mark.asyncio
async def test_the_home_address_of_a_cart_on_home_delivery(home_delivery_cart: FakeBilka) -> None:
    async with home_delivery_cart.client() as http, client(home_delivery_cart, http) as bilka:
        home = await bilka.get_home_address()

    assert home == HomeAddress(
        id="185595",
        zip_code="9999",
        recipient_name="Kunde Eksempel",
        store_id="1661",
        minimum_order=Decimal("500.00"),
    )


@pytest.mark.asyncio
async def test_no_home_address_before_one_was_seen(logged_in_cart: FakeBilka) -> None:
    async with logged_in_cart.client() as http, client(logged_in_cart, http) as bilka:
        assert await bilka.get_home_address() is None


@pytest.mark.asyncio
async def test_the_home_address_is_remembered_after_the_cart_moves_away(
    home_delivery_cart: FakeBilka,
) -> None:
    async with home_delivery_cart.client() as http, client(home_delivery_cart, http) as bilka:
        await bilka.get_cart()
        home_delivery_cart.cart_contents = load_fixture("cart_logged_in.json")
        home = await bilka.get_home_address()

    assert home == HomeAddress(id="185595", zip_code="9999")


@pytest.mark.asyncio
async def test_the_home_address_is_remembered_from_the_list_of_places(
    home_delivery_cart: FakeBilka,
) -> None:
    async with home_delivery_cart.client() as http, client(home_delivery_cart, http) as bilka:
        await bilka.get_pickup_points()
        home_delivery_cart.cart_contents = load_fixture("cart_logged_in.json")
        home = await bilka.get_home_address()

    assert home == HomeAddress(id="185595", zip_code="9999")


@pytest.mark.asyncio
async def test_the_saved_session_keeps_only_the_home_addresss_id_and_zip_code(
    home_delivery_cart: FakeBilka, storage: MemoryTokenStorage
) -> None:
    async with (
        home_delivery_cart.client() as http,
        client(home_delivery_cart, http, storage) as bilka,
    ):
        await bilka.get_cart()

    assert storage.data is not None
    assert storage.data["home_address"] == {"id": "185595", "zip_code": "9999"}
    assert "Kunde Eksempel" not in str(storage.data)


@pytest.mark.asyncio
async def test_a_new_client_on_the_same_storage_remembers_the_home_address(
    home_delivery_cart: FakeBilka, storage: MemoryTokenStorage
) -> None:
    async with (
        home_delivery_cart.client() as http,
        client(home_delivery_cart, http, storage) as bilka,
    ):
        await bilka.get_cart()
    home_delivery_cart.cart_contents = load_fixture("cart_logged_in.json")

    async with (
        home_delivery_cart.client() as http,
        BilkaToGoClient(EMAIL, token_storage=storage, httpx_client=http) as bilka,
    ):
        home = await bilka.get_home_address()

    assert home == HomeAddress(id="185595", zip_code="9999")


@pytest.mark.asyncio
async def test_logging_in_again_keeps_the_remembered_home_address(
    home_delivery_cart: FakeBilka, storage: MemoryTokenStorage
) -> None:
    async with (
        home_delivery_cart.client() as http,
        client(home_delivery_cart, http, storage) as bilka,
    ):
        await bilka.get_cart()

    async with (
        home_delivery_cart.client() as http,
        client(home_delivery_cart, http, storage) as bilka,
    ):
        await bilka.login()

    assert storage.data is not None
    assert storage.data["home_address"] == {"id": "185595", "zip_code": "9999"}


@pytest.mark.asyncio
async def test_another_accounts_home_address_is_not_remembered(
    home_delivery_cart: FakeBilka, storage: MemoryTokenStorage
) -> None:
    async with (
        home_delivery_cart.client() as http,
        client(home_delivery_cart, http, storage) as bilka,
    ):
        await bilka.get_cart()
    assert storage.data is not None
    storage.data["email"] = "someone.else@example.com"
    home_delivery_cart.cart_contents = load_fixture("cart_logged_in.json")

    async with (
        home_delivery_cart.client() as http,
        client(home_delivery_cart, http, storage) as bilka,
    ):
        assert await bilka.get_home_address() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saved",
    [None, "185595", {"id": "185595"}, {"id": 185595, "zip_code": "9999"}],
    ids=["missing", "not_an_object", "no_zip_code", "not_text"],
)
async def test_a_saved_session_without_a_usable_home_address_loads_as_before(
    logged_in_cart: FakeBilka, storage: MemoryTokenStorage, saved: object
) -> None:
    async with logged_in_cart.client() as http, client(logged_in_cart, http, storage) as bilka:
        await bilka.get_cart()
    assert storage.data is not None
    if saved is None:
        storage.data.pop("home_address", None)
    else:
        storage.data["home_address"] = saved
    logins = len(logged_in_cart.calls("gigya_login"))

    async with (
        logged_in_cart.client() as http,
        BilkaToGoClient(EMAIL, token_storage=storage, httpx_client=http) as bilka,
    ):
        assert await bilka.get_home_address() is None

    assert len(logged_in_cart.calls("gigya_login")) == logins
