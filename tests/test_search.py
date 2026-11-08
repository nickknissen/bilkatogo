import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from bilkatogo import (
    BilkaToGoClient,
    BilkaUnreachableError,
    NoPickupPointError,
    Offer,
    Product,
    SearchSort,
    UnexpectedResponseError,
    UnknownPickupPointError,
)
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, algolia_query, load_fixture


@pytest.mark.asyncio
async def test_search_finds_products_at_the_carts_store(bilka: FakeBilka) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        products = await client.search("skyr")

    [request] = bilka.calls("algolia")
    query = algolia_query(request)
    assert query["indexName"] == "prod_BILKATOGO_PRODUCTS"
    assert query["query"] == "skyr"
    assert query["filters"] == "isInAssortmentIn:1653 AND nonsearchable:false"
    # Like the website, fall back to fewer words rather than find nothing.
    assert query["removeWordsIfNoResults"] == "allOptional"
    assert "article" not in json.loads(query["attributesToRetrieve"])

    assert [product.article_number for product in products] == ["19753", "128298"]
    plain = products[0]
    assert plain.name == "Skyr m. vanilje 0,2% fedt u. tilsat sukker"
    assert plain.brand == "Cheasy"
    assert plain.store_id == "1653"
    assert plain.price == Decimal("30.95")
    assert plain.unit_price == Decimal("30.95")
    assert plain.unit == "kg"
    assert plain.ean == "5711953011399"
    assert plain.size == "1 kg"
    assert plain.category == (
        "Mejeri & køl > Mejeri > Yoghurt & syrnede mælkeprodukter > Skyr > Skyr m. smag"
    )
    assert plain.in_stock is True
    assert plain.offer is None
    assert plain.multibuy_offer is None


async def search(bilka: FakeBilka, text: str, **kwargs: Any) -> list[Product]:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.search(text, **kwargs)


@pytest.mark.asyncio
async def test_skarp_pris_offer_lowers_the_price(bilka: FakeBilka) -> None:
    [_, product] = await search(bilka, "skyr")

    assert product.article_number == "128298"
    assert product.price == Decimal("39.31")
    assert product.unit_price == Decimal("49.14")
    assert product.offer == Offer(
        label="Skarp pris",
        price=Decimal("25.00"),
        unit_price=Decimal("31.25"),
        ends=None,
        limit_per_customer=None,
    )
    assert product.multibuy_offer is None


@pytest.mark.asyncio
async def test_dated_offer_keeps_the_price_from_before_it(bilka: FakeBilka) -> None:
    [product] = await search(bilka, "kyllingebrystfilet")

    assert product.price == Decimal("64.95")
    assert product.unit_price == Decimal("99.92")
    assert product.offer == Offer(
        label="Spar 4,95",
        price=Decimal("60.00"),
        unit_price=Decimal("92.31"),
        ends=date(2026, 10, 29),
        limit_per_customer=None,
    )


@pytest.mark.asyncio
async def test_multibuy_offer_is_bilkas_text_and_not_an_offer(bilka: FakeBilka) -> None:
    [product] = await search(bilka, "bananer")

    assert product.price == Decimal("2.75")
    assert product.unit_price == Decimal("2.75")
    assert product.unit == "stk"
    assert product.offer is None
    assert product.multibuy_offer == "Mix 10 for 25,00"


@pytest.mark.asyncio
async def test_se_her_placeholder_offer_is_labelled_tilbud(bilka: FakeBilka) -> None:
    [danbo] = load_fixture("algolia_offers.json")["hits"]
    bilka.products.append(
        {**danbo, "isInAssortmentIn": [1653], "isInOffer": [1653], "inStockStore": [1653]}
    )

    [product] = await search(bilka, "danbo")

    assert product.offer == Offer(
        label="Tilbud",
        price=Decimal("25.00"),
        unit_price=Decimal("104.17"),
        ends=None,
        limit_per_customer=None,
    )
    assert product.price == Decimal("30.95")


@pytest.mark.asyncio
async def test_empty_ean_and_size_are_none(bilka: FakeBilka) -> None:
    bilka.products[0].update(gtin="", units=0, unitsOfMeasure="")

    [product, _] = await search(bilka, "skyr")

    assert product.ean is None
    assert product.size is None


@pytest.mark.asyncio
async def test_size_is_written_as_the_website_does(bilka: FakeBilka) -> None:
    bilka.products[0].update(units=1.5, unitsOfMeasure="l")

    [product, _] = await search(bilka, "skyr")

    assert product.size == "1,5 l"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "product_type", "category"),
    [
        ("Frugt & grønt > Frugt > Bananer", "", "Frugt & grønt > Frugt > Bananer"),
        ("Frugt & grønt > Frugt > Bananer", "Bananer", "Frugt & grønt > Frugt > Bananer"),
        ("", "Skyr m. smag", "Skyr m. smag"),
        (None, None, None),
    ],
)
async def test_category_is_the_category_path_and_product_type_either_alone_or_none(
    bilka: FakeBilka, path: str | None, product_type: str | None, category: str | None
) -> None:
    bilka.products[0].update(deepestCategoryPath=path, productType=product_type)

    [product, _] = await search(bilka, "skyr")

    assert product.category == category


@pytest.mark.asyncio
async def test_product_out_of_stock_at_the_store(bilka: FakeBilka) -> None:
    [mozzarella] = await search(bilka, "mozzarella")
    [chicken] = await search(bilka, "kyllingebrystfilet")

    assert mozzarella.in_stock is False
    assert chicken.in_stock is False


@pytest.mark.asyncio
async def test_named_pickup_point_searches_its_store(bilka: FakeBilka) -> None:
    [product] = await search(bilka, "mozzarella", pickup_point="pickup fredericia")

    assert algolia_query(bilka.calls("algolia")[0])["filters"] == (
        "isInAssortmentIn:1662 AND nonsearchable:false"
    )
    assert bilka.calls("cart") == []
    assert product.store_id == "1662"
    assert product.in_stock is True


@pytest.mark.asyncio
async def test_unknown_pickup_point_does_not_search(bilka: FakeBilka) -> None:
    with pytest.raises(UnknownPickupPointError):
        await search(bilka, "skyr", pickup_point="Bilka Atlantis")

    assert bilka.calls("algolia") == []


@pytest.mark.asyncio
async def test_home_delivery_cart_searches_the_store_that_delivers_it(
    home_delivery_cart: FakeBilka,
) -> None:
    [product] = await search(home_delivery_cart, "mozzarella")

    assert algolia_query(home_delivery_cart.calls("algolia")[0])["filters"] == (
        "isInAssortmentIn:1661 AND nonsearchable:false"
    )
    assert product.store_id == "1661"


@pytest.mark.asyncio
async def test_cart_without_a_pickup_point_asks_for_one(bilka: FakeBilka) -> None:
    bilka.cart_contents = {**bilka.cart_contents, "deliveryAddress": None}

    with pytest.raises(NoPickupPointError, match="Pickup Point or Home Delivery address"):
        await search(bilka, "skyr")

    assert bilka.calls("algolia") == []


@pytest.mark.asyncio
async def test_limit_caps_the_number_of_products(bilka: FakeBilka) -> None:
    products = await search(bilka, "skyr", limit=1)

    assert algolia_query(bilka.calls("algolia")[0])["hitsPerPage"] == "1"
    assert [product.article_number for product in products] == ["19753"]


@pytest.mark.asyncio
async def test_offer_with_a_limit_per_customer(bilka: FakeBilka) -> None:
    store = bilka.products[1]["storeData"]["1653"]
    store["offerMax"] = 3
    store["offerMaxDescription"] = "Max 3 stk. pr. kunde"

    [_, product] = await search(bilka, "skyr")

    assert product.offer is not None
    assert product.offer.limit_per_customer == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offer_max", "limit"), [("3", 3), ("0", None), ("", None), ("lots", None), (None, None)]
)
async def test_limit_per_customer_is_a_whole_number_or_none(
    bilka: FakeBilka, offer_max: object, limit: int | None
) -> None:
    bilka.products[1]["storeData"]["1653"]["offerMax"] = offer_max

    [_, product] = await search(bilka, "skyr")

    assert product.offer is not None
    assert product.offer.limit_per_customer == limit


@pytest.mark.asyncio
async def test_unreachable_algolia_raises_unreachable(bilka: FakeBilka) -> None:
    bilka.script("algolia", httpx.ConnectError("no route to host"))

    with pytest.raises(BilkaUnreachableError, match="algolia"):
        await search(bilka, "skyr")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(403, json={"message": "Invalid Application-ID or API key"}),
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={"message": "no results"}),
    ],
)
async def test_unexpected_algolia_answer_raises_unexpected_response(
    bilka: FakeBilka, answer: httpx.Response
) -> None:
    bilka.script("algolia", answer)

    with pytest.raises(UnexpectedResponseError, match="Product search"):
        await search(bilka, "skyr")


@pytest.mark.asyncio
async def test_filters_narrow_the_search_at_the_store(bilka: FakeBilka) -> None:
    await search(bilka, "skyr", in_stock=True, on_offer=True, in_leaflet=True)

    [request] = bilka.calls("algolia")
    assert algolia_query(request)["filters"] == (
        "isInAssortmentIn:1653 AND nonsearchable:false"
        " AND inStockStore:1653 AND isInOffer:1653 AND isInCurrentLeaflet:true"
    )


def article_numbers(products: list[Product]) -> list[str]:
    return [product.article_number for product in products]


@pytest.mark.asyncio
async def test_in_stock_leaves_out_products_out_of_stock_at_the_store(bilka: FakeBilka) -> None:
    products = await search(bilka, "", in_stock=True)

    assert article_numbers(products) == ["19753", "128298", "18381"]


@pytest.mark.asyncio
async def test_on_offer_keeps_offers_and_multibuy_offers(bilka: FakeBilka) -> None:
    products = await search(bilka, "", on_offer=True)

    assert article_numbers(products) == ["128298", "18381", "169794"]


@pytest.mark.asyncio
async def test_in_leaflet_keeps_products_in_this_weeks_leaflet(bilka: FakeBilka) -> None:
    bilka.products[3]["isInCurrentLeaflet"] = True

    products = await search(bilka, "", in_leaflet=True)

    assert article_numbers(products) == ["140787"]


@pytest.mark.asyncio
async def test_sort_by_price_puts_the_cheapest_first_counting_offers(bilka: FakeBilka) -> None:
    products = await search(bilka, "", sort=SearchSort.PRICE)

    # Bananer 2.75, mozzarella 14.35, skyr on Offer 25.00 (Price 39.31), skyr 30.95,
    # chicken on Offer 60.00 (Price 64.95).
    assert article_numbers(products) == ["18381", "140787", "128298", "19753", "169794"]


@pytest.mark.asyncio
async def test_sort_by_unit_price_counts_offers_and_puts_products_without_one_last(
    bilka: FakeBilka,
) -> None:
    bilka.products[0]["storeData"]["1653"]["unitsOfMeasurePriceUnit"] = ""

    products = await search(bilka, "", sort=SearchSort.UNIT_PRICE)

    # Bananer 2.75/stk, skyr on Offer 31.25/kg (49.14 without), mozzarella 71.75/kg,
    # chicken on Offer 92.31/kg, then the skyr without a Unit Price.
    assert article_numbers(products) == ["18381", "128298", "140787", "169794", "19753"]


@pytest.mark.asyncio
async def test_sort_orders_only_the_best_matches_up_to_the_limit(bilka: FakeBilka) -> None:
    products = await search(bilka, "", limit=2, sort=SearchSort.PRICE)

    [request] = bilka.calls("algolia")
    assert algolia_query(request)["hitsPerPage"] == "2"
    # The two best matches, skyr on Offer 25.00 before skyr 30.95; the cheaper Bananer
    # further down Bilka's order is not among them.
    assert article_numbers(products) == ["128298", "19753"]
