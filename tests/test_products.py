import json
from typing import Any

import pytest

from bilkatogo import BilkaToGoClient, NoPickupPointError, Product
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, algolia_query


async def get_products(
    bilka: FakeBilka, article_numbers: list[str], **kwargs: Any
) -> list[Product]:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.get_products(article_numbers, **kwargs)


@pytest.mark.asyncio
async def test_products_by_article_number_come_in_the_order_asked(bilka: FakeBilka) -> None:
    products = await get_products(bilka, ["18381", "19753"])

    [request] = bilka.calls("algolia")
    query = algolia_query(request)
    assert query["filters"] == "(objectID:18381 OR objectID:19753) AND isInAssortmentIn:1653"
    assert query["hitsPerPage"] == "2"
    assert json.loads(query["attributesToRetrieve"]) == [
        "name",
        "brand",
        "price",
        "gtin",
        "units",
        "unitsOfMeasure",
        "productType",
        "deepestCategoryPath",
        "storeData.1653",
    ]
    assert [(product.article_number, product.name) for product in products] == [
        ("18381", "Bananer"),
        ("19753", "Skyr m. vanilje 0,2% fedt u. tilsat sukker"),
    ]
    assert products[0].price is not None
    assert products[0].store_id == "1653"


@pytest.mark.asyncio
async def test_article_numbers_bilka_does_not_know_are_left_out(bilka: FakeBilka) -> None:
    products = await get_products(bilka, ["99999", "140787"])

    assert [product.article_number for product in products] == ["140787"]


@pytest.mark.asyncio
async def test_products_kept_out_of_search_are_found_by_article_number(
    bilka: FakeBilka,
) -> None:
    bilka.products[2]["nonsearchable"] = True

    [product] = await get_products(bilka, ["18381"])

    assert product.name == "Bananer"


@pytest.mark.asyncio
async def test_named_pickup_point_looks_up_its_store(bilka: FakeBilka) -> None:
    [product] = await get_products(bilka, ["140787"], pickup_point="pickup fredericia")

    assert algolia_query(bilka.calls("algolia")[0])["filters"] == (
        "(objectID:140787) AND isInAssortmentIn:1662"
    )
    assert bilka.calls("cart") == []
    assert product.store_id == "1662"


@pytest.mark.asyncio
async def test_cart_without_a_store_asks_for_a_pickup_point(bilka: FakeBilka) -> None:
    bilka.cart_contents = {**bilka.cart_contents, "deliveryAddress": None}

    with pytest.raises(NoPickupPointError):
        await get_products(bilka, ["18381"])

    assert bilka.calls("algolia") == []


@pytest.mark.asyncio
async def test_no_article_numbers_asks_bilka_nothing(bilka: FakeBilka) -> None:
    assert await get_products(bilka, []) == []

    assert bilka.requests == []


@pytest.mark.asyncio
async def test_more_than_a_thousand_article_numbers_is_refused(bilka: FakeBilka) -> None:
    with pytest.raises(ValueError, match="1000"):
        await get_products(bilka, [str(number) for number in range(1001)])

    assert bilka.requests == []
