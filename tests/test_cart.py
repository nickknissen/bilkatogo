from datetime import date, datetime
from decimal import Decimal

import pytest
from freezegun.api import FrozenDateTimeFactory

from bilkatogo import BilkaToGoClient, Cart
from bilkatogo.const import BILKA_TIME_ZONE
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture


async def fetch(bilka: FakeBilka) -> Cart:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.get_cart()


@pytest.mark.asyncio
async def test_cart_lines_have_product_article_number_quantity_price_and_line_total(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await fetch(logged_in_cart)

    [request] = logged_in_cart.calls("cart")
    assert dict(request.url.params) == {"u": "w", "extra": "deliveryAddress,deliveryDate"}
    assert [
        (line.product, line.article_number, line.quantity, line.price, line.line_total)
        for line in cart.lines
    ] == [
        ("Bananer", "18381", 10, Decimal("2.75"), Decimal("25.00")),
        ("Agurk", "18364", 1, Decimal("7.00"), Decimal("7.00")),
        ("Yoghurt m. pære og banan 1,4% fedt", "19789", 1, Decimal("17.00"), Decimal("17.00")),
        (
            "Flydende smør og rapsolie til madlavning",
            "185370",
            1,
            Decimal("12.00"),
            Decimal("10.00"),
        ),
        ("Mini Mix", "75191", 1, Decimal("29.00"), Decimal("29.00")),
    ]


@pytest.mark.asyncio
async def test_cart_total_and_breakdown_in_kroner(logged_in_cart: FakeBilka) -> None:
    cart = await fetch(logged_in_cart)

    assert "Papirpose" not in [line.product for line in cart.lines]
    assert "Pakkeservice" not in [line.product for line in cart.lines]
    assert str(cart.total) == "127.00"
    assert str(cart.service_fee) == "39.00"
    assert str(cart.packaging) == "0.00"
    assert str(cart.deposit) == "0.00"
    assert str(cart.discount) == "9.50"


@pytest.mark.asyncio
async def test_cart_discount_adds_offers_membership_personal_offers_and_vouchers(
    logged_in_cart: FakeBilka,
) -> None:
    logged_in_cart.cart_contents["stat"].update(
        {
            "promo": 1000,
            "membership_offer_discount": 250,
            "personalized_offer_discount": 125,
            "vouchers": [{"amount": 5000}],
            "deposit": 150,
            "packaging_price": 400,
        }
    )

    cart = await fetch(logged_in_cart)

    assert str(cart.discount) == "63.75"
    assert str(cart.deposit) == "1.50"
    assert str(cart.packaging) == "4.00"


@pytest.mark.asyncio
async def test_cart_has_the_chosen_pickup_point(logged_in_cart: FakeBilka) -> None:
    cart = await fetch(logged_in_cart)

    assert cart.pickup_point is not None
    assert cart.pickup_point.name == "Bilka Vejle"
    assert cart.pickup_point.store_id == "1661"


@pytest.mark.asyncio
async def test_pickup_cart_is_not_home_delivery_and_has_its_store(bilka: FakeBilka) -> None:
    cart = await fetch(bilka)

    assert cart.home_delivery is False
    assert cart.pickup_point is not None
    assert cart.store_id == "1653"


@pytest.mark.asyncio
async def test_home_delivery_cart_has_no_pickup_point_but_a_store(
    home_delivery_cart: FakeBilka,
) -> None:
    cart = await fetch(home_delivery_cart)

    assert cart.home_delivery is True
    assert cart.pickup_point is None
    assert cart.store_id == "1661"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "marker",
    [
        {"dawa_uid": None},
        {"recipient_name": "", "dawa_uid": "0a3f50a0-0000-0000-0000-000000000000"},
    ],
    ids=["by_name", "by_dawa_uid"],
)
async def test_either_home_address_field_marks_home_delivery(
    home_delivery_cart: FakeBilka, marker: dict
) -> None:
    home_delivery_cart.cart_contents["deliveryAddress"].update(marker)

    cart = await fetch(home_delivery_cart)

    assert cart.home_delivery is True
    assert cart.pickup_point is None


@pytest.mark.asyncio
async def test_home_delivery_cart_has_its_minimum_order_and_delivery_fee(
    home_delivery_cart: FakeBilka,
) -> None:
    cart = await fetch(home_delivery_cart)

    assert cart.minimum_order == Decimal("500.00")
    assert cart.minimum_left == Decimal("398.60")
    assert cart.delivery_fee == Decimal("39.00")
    assert cart.next_available_date == date(2026, 11, 9)


@pytest.mark.asyncio
async def test_delivery_fee_is_not_a_cart_line(home_delivery_cart: FakeBilka) -> None:
    cart = await fetch(home_delivery_cart)

    assert [line.product for line in cart.lines] == ["Brun farin", "Flormelis"]
    assert cart.total == Decimal("101.40")


@pytest.mark.asyncio
async def test_pickup_cart_has_no_minimum_order_or_delivery_fee(
    logged_in_cart: FakeBilka,
) -> None:
    cart = await fetch(logged_in_cart)

    assert cart.minimum_order is None
    assert cart.minimum_left == Decimal("0.00")
    assert cart.delivery_fee == Decimal("0.00")
    assert cart.next_available_date == date(2026, 10, 27)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "snart", "2026-13-01"])
async def test_unreadable_next_available_date_is_none(
    logged_in_cart: FakeBilka, value: str | None
) -> None:
    logged_in_cart.cart_contents["deliveryAddress"]["nextAvailableDeliveryDate"] = value

    cart = await fetch(logged_in_cart)

    assert cart.next_available_date is None


@pytest.mark.asyncio
async def test_minimum_order_not_reached_is_a_warning_without_html(
    home_delivery_cart: FakeBilka,
) -> None:
    cart = await fetch(home_delivery_cart)

    assert cart.warnings == [
        "398,60 kr. fra minimumskøb på 500,00 kr. alt inkl. for at få hjemmelevering"
    ]


@pytest.mark.asyncio
async def test_minimum_order_reached_is_no_warning(home_delivery_cart: FakeBilka) -> None:
    home_delivery_cart.cart_contents["stat"]["minimum_left"] = 0

    cart = await fetch(home_delivery_cart)

    assert cart.warnings == []


@pytest.mark.asyncio
async def test_bilkas_cart_messages_are_warnings(logged_in_cart: FakeBilka) -> None:
    logged_in_cart.cart_contents["messages"] = [
        {"text": "Vi har travlt i weekenden", "type": "info"},
        "Butikken lukker kl. 20",
    ]

    cart = await fetch(logged_in_cart)

    assert cart.warnings == ["Vi har travlt i weekenden", "Butikken lukker kl. 20"]


@pytest.mark.asyncio
async def test_chosen_pickup_slot_that_lies_ahead(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-01T15:59:00+01:00")

    cart = await fetch(logged_in_cart)

    assert cart.pickup_slot is not None
    assert cart.pickup_slot.starts_at == datetime(2026, 11, 1, 14, tzinfo=BILKA_TIME_ZONE)
    assert cart.pickup_slot.ends_at == datetime(2026, 11, 1, 16, tzinfo=BILKA_TIME_ZONE)


@pytest.mark.asyncio
async def test_chosen_pickup_slot_that_has_ended_is_none(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-01T16:00:01+01:00")

    cart = await fetch(logged_in_cart)

    assert cart.pickup_slot is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery_date",
    [
        None,
        {"deliveryDate": "2026-11-01", "intervalStart": "14:00"},
        {"deliveryDate": "i morgen", "intervalStart": "14:00", "intervalEnd": "16:00"},
        {"deliveryDate": "2026-11-01", "intervalStart": "14:00", "intervalEnd": "sent"},
    ],
    ids=["missing", "no_end", "bad_date", "bad_time"],
)
async def test_unreadable_chosen_pickup_slot_is_none(
    logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory, delivery_date: dict | None
) -> None:
    freezer.move_to("2026-10-30T12:00:00+01:00")
    logged_in_cart.cart_contents["deliveryDate"] = delivery_date

    cart = await fetch(logged_in_cart)

    assert cart.pickup_slot is None


@pytest.mark.asyncio
async def test_cart_without_a_pickup_point_has_none(logged_in_cart: FakeBilka) -> None:
    logged_in_cart.cart_contents["deliveryAddress"] = None

    cart = await fetch(logged_in_cart)

    assert cart.pickup_point is None


@pytest.mark.asyncio
async def test_empty_cart_has_no_cart_lines(bilka: FakeBilka) -> None:
    bilka.cart_contents = load_fixture("cart.json")

    cart = await fetch(bilka)

    assert cart.lines == []
    assert cart.total == Decimal("0.00")
    assert cart.service_fee == Decimal("0.00")
