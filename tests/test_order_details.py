import json
import logging
from datetime import date, time
from decimal import Decimal

import pytest

from bilkatogo import (
    BilkaToGoClient,
    BilkaToGoError,
    OrderDetails,
    OrderNotFoundError,
    OrderStatus,
)
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka


async def fetch(bilka: FakeBilka, order_number: str) -> OrderDetails:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.get_order(order_number)


@pytest.mark.asyncio
async def test_get_order_fetches_one_order_with_pickup_code(bilka: FakeBilka) -> None:
    details = await fetch(bilka, "900000000001")

    [request] = bilka.calls("order_details")
    assert request.method == "GET"
    assert dict(request.url.params) == {"u": "w", "orderId": "900000000001"}

    assert details.order_number == "900000000001"
    assert details.status is OrderStatus.PICKED_UP
    assert details.status_code == "Faktureret"
    assert details.pickup_slot is not None
    assert details.pickup_slot.date == date(2026, 10, 27)
    assert details.pickup_slot.start == time(12, 0)
    assert details.pickup_slot.end == time(14, 0)
    assert details.pickup_point is not None
    assert details.pickup_point.name == "Bilka Eksempelby"
    assert details.home_delivery is False
    assert details.pickup_code is not None
    assert details.pickup_code.code == "12345678"
    assert str(details.order_total) == "509.00"


@pytest.mark.asyncio
async def test_order_lines_are_flattened_across_categories(bilka: FakeBilka) -> None:
    details = await fetch(bilka, "900000000001")

    assert [(line.category, line.name, line.quantity, line.amount) for line in details.lines] == [
        ("Frugt & grønt", "Æbler", 4, Decimal("12.00")),
        ("Frugt & grønt", "Rugbrød", 1, Decimal("25.00")),
        ("Mejeri", "Letmælk", 2, Decimal("11.00")),
    ]
    assert details.lines[1].article_number == "1003"
    assert details.lines[1].substituted is False
    assert (
        details.lines[1]._raw
        == bilka.details["900000000001"]["order"]["lines"][0]["lines"][0]["orderlines"][1]
    )


@pytest.mark.asyncio
async def test_substitutions_pair_original_and_replacement_product(bilka: FakeBilka) -> None:
    details = await fetch(bilka, "900000000001")

    assert [
        (
            s.original_product,
            s.original_article_number,
            s.ordered_quantity,
            s.replacement_product,
            s.replacement_article_number,
            s.quantity,
        )
        for s in details.substitutions
    ] == [
        ("Æbler", "1001", 4, "Pærer", "1002", 4),
        ("Letmælk", "1004", 2, "Minimælk", "1005", 3),
    ]
    assert details.lines[0].substituted is True
    assert details.substitutions[0]._raw == details.lines[0]._raw


@pytest.mark.asyncio
async def test_order_without_substitutions_has_none(bilka: FakeBilka) -> None:
    details = await fetch(bilka, "900000000002")

    assert details.substitutions == []
    assert [line.name for line in details.lines] == ["Æbler", "Havregryn"]
    assert not any(line.substituted for line in details.lines)


def orderlines(bilka: FakeBilka, order_number: str) -> list[dict]:
    return [
        line
        for group in bilka.details[order_number]["order"]["lines"]
        for block in group["lines"]
        for line in block["orderlines"]
    ]


@pytest.mark.asyncio
async def test_a_missing_item_is_logged_with_its_raw_line_until_its_shape_is_confirmed(
    bilka: FakeBilka, caplog: pytest.LogCaptureFixture
) -> None:
    # The Missing Item shape is synthetic until a real capture replaces it (#27).
    orderlines(bilka, "900000000002")[1]["unavailable_reason"] = "Udsolgt"

    with caplog.at_level(logging.WARNING, logger="bilkatogo"):
        details = await fetch(bilka, "900000000002")

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "Missing Item" in record.getMessage()
    assert "issues/27" in record.getMessage()
    assert "Havregryn" in record.getMessage()
    assert "900000000002" in record.getMessage()
    raw_line = orderlines(bilka, "900000000002")[1]
    assert json.dumps(raw_line, ensure_ascii=False) in record.getMessage()
    assert details.lines[1].unavailable_reason == "Udsolgt"
    assert [item.product for item in details.missing_items] == ["Havregryn"]
    assert details.substitutions == []


@pytest.mark.asyncio
async def test_an_unavailable_line_that_is_neither_is_logged_as_unclassified(
    bilka: FakeBilka, caplog: pytest.LogCaptureFixture
) -> None:
    # Looks like a Substitution (it has a child) but lacks Bilka's "erstattet" tagline.
    line = orderlines(bilka, "900000000001")[0]
    line["unavailable_reason"] = "Udsolgt"
    line["tagline"] = None

    with caplog.at_level(logging.WARNING, logger="bilkatogo"):
        details = await fetch(bilka, "900000000001")

    [record] = caplog.records
    assert "neither a Substitution nor a Missing Item" in record.getMessage()
    assert "Æbler" in record.getMessage()
    assert json.dumps(line, ensure_ascii=False) in record.getMessage()
    assert details.lines[0].substituted is False
    assert len(details.substitutions) == 1
    assert details.missing_items == []


@pytest.mark.asyncio
async def test_unavailable_reason_on_a_substitution_is_not_logged(
    bilka: FakeBilka, caplog: pytest.LogCaptureFixture
) -> None:
    orderlines(bilka, "900000000001")[0]["unavailable_reason"] = "Udsolgt"

    with caplog.at_level(logging.WARNING, logger="bilkatogo"):
        details = await fetch(bilka, "900000000001")

    assert caplog.records == []
    assert len(details.substitutions) == 2


@pytest.mark.asyncio
async def test_missing_item_with_its_whole_quantity_missing(bilka: FakeBilka) -> None:
    details = await fetch(bilka, "900000000003")

    havregryn = next(item for item in details.missing_items if item.product == "Havregryn")
    assert (
        havregryn.article_number,
        havregryn.ordered_quantity,
        havregryn.missing_quantity,
    ) == ("1006", 1, 1)


@pytest.mark.asyncio
async def test_missing_item_packed_short(bilka: FakeBilka) -> None:
    details = await fetch(bilka, "900000000003")

    skyr = next(item for item in details.missing_items if item.product == "Skyr")
    assert (skyr.article_number, skyr.ordered_quantity, skyr.missing_quantity) == ("932058", 4, 2)


@pytest.mark.asyncio
async def test_a_substitution_is_not_a_missing_item(bilka: FakeBilka) -> None:
    details = await fetch(bilka, "900000000003")

    assert [item.product for item in details.missing_items] == ["Skyr", "Havregryn"]
    assert [s.original_product for s in details.substitutions] == ["Letmælk"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "odd",
    [
        {"unavailable_reason": {"code": "OOS"}},
        {"unavailable_reason": "Ukendt årsag", "quantity": None},
        {"unavailable_quantity": "to"},
        {"unavailable_quantity": None, "product": None},
    ],
)
async def test_odd_unavailable_lines_do_not_break_parsing(bilka: FakeBilka, odd: dict) -> None:
    orderlines(bilka, "900000000003")[2].update(odd)

    details = await fetch(bilka, "900000000003")

    assert [item.product for item in details.missing_items][0] == "Skyr"
    assert len(details.missing_items) == 2
    assert len(details.lines) == 4


@pytest.mark.asyncio
async def test_order_without_unavailable_lines_has_no_missing_items(bilka: FakeBilka) -> None:
    details = await fetch(bilka, "900000000001")

    assert details.missing_items == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scripted", [None, {"code": 200, "msg": "ok"}])
async def test_unknown_order_number_raises_bilka_to_go_error(
    bilka: FakeBilka, scripted: dict | None
) -> None:
    if scripted is not None:
        bilka.script("order_details", scripted)

    with pytest.raises(BilkaToGoError):
        await fetch(bilka, "900000000099")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [{}, {"order": None}])
async def test_an_answer_without_the_order_raises_order_not_found(
    bilka: FakeBilka, answer: dict
) -> None:
    bilka.script("order_details", answer)

    with pytest.raises(OrderNotFoundError) as caught:
        await fetch(bilka, "900000000099")

    assert caught.value.order_number == "900000000099"
