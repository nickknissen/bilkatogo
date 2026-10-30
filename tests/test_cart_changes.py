from collections.abc import Awaitable, Callable

import httpx
import pytest

from bilkatogo import BilkaToGoClient, Cart, CartChangeRejectedError
from tests.conftest import MemoryTokenStorage
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture


async def change(bilka: FakeBilka, action: Callable[[BilkaToGoClient], Awaitable[Cart]]) -> Cart:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await action(client)


def quantities(cart: Cart) -> dict[str, int]:
    return {line.article_number: line.quantity for line in cart.lines}


@pytest.mark.asyncio
async def test_set_cart_quantity_sets_the_absolute_quantity_by_article_number(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await change(logged_in_cart, lambda client: client.set_cart_quantity("18364", 3))

    [request] = logged_in_cart.calls("change_line_count")
    assert dict(request.url.params) == {
        "u": "w",
        "productId": "18364",
        "count": "3",
        "fullCart": "0",
    }
    assert quantities(cart)["18364"] == 3
    assert cart.warnings == []


@pytest.mark.asyncio
async def test_a_message_on_an_applied_change_is_returned_as_a_warning(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.change_answer = load_fixture("change_line_count_warning.json")

    cart = await change(logged_in_cart, lambda client: client.set_cart_quantity("18364", 99))

    assert quantities(cart)["18364"] == 99
    assert cart.warnings == [
        "Ring venligst til kundeservice på 89 30 30 00 inden bestilling, "
        "for at sikre det ønskede antal"
    ]


@pytest.mark.asyncio
async def test_an_offer_limit_message_on_an_applied_change_is_a_warning(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.change_answer = {
        **load_fixture("change_line_count.json"),
        "offerLimitMessage": "Maks. 2 til tilbudspris",
    }

    cart = await change(logged_in_cart, lambda client: client.set_cart_quantity("18364", 2))

    assert cart.warnings == ["Maks. 2 til tilbudspris"]


@pytest.mark.asyncio
async def test_a_change_applied_with_a_different_quantity_is_rejected_with_bilkas_message(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.max_quantity = 2
    logged_in_cart.change_answer = {
        **load_fixture("change_line_count.json"),
        "offerLimitMessage": "Maks. 2 pr. kunde",
    }

    with pytest.raises(CartChangeRejectedError, match="Maks. 2 pr. kunde") as rejected:
        await change(logged_in_cart, lambda client: client.set_cart_quantity("18364", 5))

    assert rejected.value.article_number == "18364"
    assert rejected.value.requested == 5
    assert rejected.value.applied == 2


@pytest.mark.asyncio
async def test_a_change_answer_without_the_applied_quantity_is_rejected(
    logged_in_cart: FakeBilka,
) -> None:
    answer = load_fixture("change_line_count.json")
    del answer["productAmount"]
    logged_in_cart.script("change_line_count", answer)

    with pytest.raises(
        CartChangeRejectedError, match="Bilka did not say what quantity of 18364 it set"
    ) as rejected:
        await change(logged_in_cart, lambda client: client.set_cart_quantity("18364", 3))

    assert rejected.value.requested == 3
    assert rejected.value.applied is None
    assert logged_in_cart.calls("cart") == []


@pytest.mark.asyncio
async def test_an_unavailable_product_is_rejected_with_bilkas_message_without_logging_in_again(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.script("change_line_count", load_fixture("change_line_count_unavailable.json"))

    with pytest.raises(
        CartChangeRejectedError, match="er desværre ikke aktuelt tilgængeligt"
    ) as rejected:
        await change(logged_in_cart, lambda client: client.set_cart_quantity("88672", 3))

    assert rejected.value.article_number == "88672"
    assert rejected.value.requested == 3
    assert rejected.value.applied is None
    assert len(logged_in_cart.calls("change_line_count")) == 1
    assert len(logged_in_cart.calls("gigya_login")) == 1


@pytest.mark.asyncio
async def test_add_to_cart_adds_to_the_quantity_of_an_existing_cart_line(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await change(logged_in_cart, lambda client: client.add_to_cart("18381", quantity=2))

    [request] = logged_in_cart.calls("change_line_count")
    assert request.url.params["count"] == "12"
    assert quantities(cart)["18381"] == 12


@pytest.mark.asyncio
async def test_add_to_cart_creates_a_cart_line_for_a_product_not_in_the_cart(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await change(logged_in_cart, lambda client: client.add_to_cart("19753"))

    [request] = logged_in_cart.calls("change_line_count")
    assert request.url.params["count"] == "1"
    [line] = [line for line in cart.lines if line.article_number == "19753"]
    assert (line.product, line.quantity) == ("Skyr m. vanilje 0,2% fedt u. tilsat sukker", 1)
    assert len(cart.lines) == 6


@pytest.mark.asyncio
async def test_remove_from_cart_sets_the_quantity_to_zero_and_drops_the_cart_line(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await change(logged_in_cart, lambda client: client.remove_from_cart("18364"))

    [request] = logged_in_cart.calls("change_line_count")
    assert request.url.params["count"] == "0"
    assert "18364" not in quantities(cart)
    assert len(cart.lines) == 4


@pytest.mark.asyncio
async def test_remove_from_cart_takes_a_quantity_off_the_cart_line(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await change(logged_in_cart, lambda client: client.remove_from_cart("18381", quantity=3))

    [request] = logged_in_cart.calls("change_line_count")
    assert request.url.params["count"] == "7"
    assert quantities(cart)["18381"] == 7


@pytest.mark.asyncio
async def test_removing_more_than_the_cart_line_holds_drops_it(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await change(logged_in_cart, lambda client: client.remove_from_cart("18364", quantity=5))

    [request] = logged_in_cart.calls("change_line_count")
    assert request.url.params["count"] == "0"
    assert "18364" not in quantities(cart)


@pytest.mark.asyncio
async def test_clear_cart_empties_the_cart_and_keeps_the_pickup_point(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await change(logged_in_cart, lambda client: client.clear_cart())

    [request] = logged_in_cart.calls("empty_cart")
    assert dict(request.url.params) == {"u": "w", "extra": "deliveryAddress,deliveryDate"}
    assert cart.lines == []
    assert cart.warnings == []
    assert cart.pickup_point is not None
    assert cart.pickup_point.name == "Bilka Vejle"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "action"),
    [
        ("change_line_count", lambda client: client.set_cart_quantity("18364", 2)),
        ("empty_cart", lambda client: client.clear_cart()),
    ],
)
async def test_an_expired_session_during_a_change_logs_in_again_and_retries_once(
    logged_in_cart: FakeBilka,
    storage: MemoryTokenStorage,
    route: str,
    action: Callable[[BilkaToGoClient], Awaitable[Cart]],
) -> None:
    async with (
        logged_in_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await client.login()
    logged_in_cart.script(route, httpx.Response(401, json={"code": 401, "msg": "Unauthorized"}))

    async with (
        logged_in_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await action(client)

    assert len(logged_in_cart.calls(route)) == 2
    assert len(logged_in_cart.calls("gigya_login")) == 2
