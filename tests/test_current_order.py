from datetime import date, time

import pytest

from bilkatogo import BilkaToGoClient, OrderDetails, OrderHistory, OrderStatus
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture


def history(bilka: FakeBilka, *orders: tuple[str, str, str | None, str | None]) -> None:
    template = load_fixture("order_history.json")["orders"][0]
    details = load_fixture("order_details_plain.json")
    bilka.orders = []
    bilka.details = {}
    for order_number, status, day, start in orders:
        slot = {"deliveryDate": day, "deliveryInterval": {"start": start, "end": "23:00:00"}}
        if day is None:
            slot = {"deliveryDate": None, "deliveryInterval": None}
        bilka.orders.append({**template, "orderId": order_number, "status": status, **slot})
        bilka.details[order_number] = {
            "order": {**details["order"], "orderId": order_number, "status": status, **slot}
        }


async def current(bilka: FakeBilka) -> OrderDetails | None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.get_current_order()


async def order_history(bilka: FakeBilka) -> OrderHistory:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.get_order_history()


@pytest.mark.asyncio
async def test_order_history_carries_the_scanned_orders_and_their_current_order(
    bilka: FakeBilka,
) -> None:
    history(
        bilka,
        ("900000000003", "Bestilt", "2026-11-05", "10:00:00"),
        ("900000000002", "Pakket", "2026-11-03", "16:00:00"),
        ("900000000001", "Faktureret", "2026-10-27", "12:00:00"),
    )

    found = await order_history(bilka)

    assert [order.order_number for order in found.orders] == [
        "900000000003",
        "900000000002",
        "900000000001",
    ]
    assert found.current_order is not None
    assert found.current_order.order_number == "900000000002"
    assert len(bilka.calls("order_history")) == 1
    assert bilka.calls("order_details") == []


@pytest.mark.asyncio
async def test_an_empty_order_history_has_no_current_order(bilka: FakeBilka) -> None:
    bilka.orders = []

    found = await order_history(bilka)

    assert found.orders == []
    assert found.current_order is None


@pytest.mark.asyncio
async def test_order_history_holds_the_latest_20_orders(bilka: FakeBilka) -> None:
    history(
        bilka,
        *[(f"9000000000{n:02}", "Faktureret", "2026-10-27", "12:00:00") for n in range(25, 0, -1)],
    )

    found = await order_history(bilka)

    assert [order.order_number for order in found.orders][:1] == ["900000000025"]
    assert len(found.orders) == 20


@pytest.mark.asyncio
async def test_no_orders_means_no_current_order(bilka: FakeBilka) -> None:
    bilka.orders = []

    assert await current(bilka) is None
    assert bilka.calls("order_details") == []


@pytest.mark.asyncio
async def test_unfinished_order_with_the_earliest_pickup_slot_wins(bilka: FakeBilka) -> None:
    history(
        bilka,
        ("900000000004", "Bestilt", "2026-11-05", "10:00:00"),
        ("900000000003", "Pakket", "2026-11-03", "16:00:00"),
        ("900000000002", "Bestilt", "2026-11-03", "12:00:00"),
        ("900000000001", "Faktureret", "2026-10-27", "12:00:00"),
    )

    details = await current(bilka)

    assert details is not None
    assert details.order_number == "900000000002"
    assert [r.url.params["orderId"] for r in bilka.calls("order_details")] == ["900000000002"]


@pytest.mark.asyncio
async def test_without_unfinished_orders_the_most_recently_finished_wins(
    bilka: FakeBilka,
) -> None:
    history(
        bilka,
        ("900000000004", "Annulleret", "2026-10-20", "10:00:00"),
        ("900000000003", "Uafhentet", "2026-10-27", "16:00:00"),
        ("900000000002", "Fuldt udsolgt", "2026-10-27", "12:00:00"),
        ("900000000001", "Faktureret", "2026-10-13", "12:00:00"),
    )

    details = await current(bilka)

    assert details is not None
    assert details.order_number == "900000000003"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("orders", "expected"),
    [
        (
            [
                ("900000000003", "Ubehandlet", None, None),
                ("900000000002", "Bestilt", "2026-11-09", "12:00:00"),
                ("900000000001", "Faktureret", "2026-10-27", "12:00:00"),
            ],
            "900000000002",
        ),
        (
            [
                ("900000000002", "Ubehandlet", None, None),
                ("900000000001", "Faktureret", "2026-10-27", "12:00:00"),
            ],
            "900000000002",
        ),
        (
            [
                ("900000000002", "Annulleret", None, None),
                ("900000000001", "Faktureret", "2026-10-27", "12:00:00"),
            ],
            "900000000001",
        ),
        (
            [
                ("900000000002", "Annulleret", None, None),
                ("900000000001", "Annulleret", None, None),
            ],
            "900000000002",
        ),
    ],
    ids=[
        "unfinished-without-slot-ranks-after-slotted",
        "unfinished-without-slot-beats-finished",
        "finished-without-slot-is-least-recent",
        "no-slots-newest-in-history-wins",
    ],
)
async def test_orders_without_a_pickup_slot(
    bilka: FakeBilka, orders: list[tuple[str, str, str | None, str | None]], expected: str
) -> None:
    history(bilka, *orders)

    details = await current(bilka)

    assert details is not None
    assert details.order_number == expected


@pytest.mark.asyncio
async def test_current_order_carries_the_summary(bilka: FakeBilka) -> None:
    history(bilka, ("900000000002", "Pakket", "2026-11-03", "16:00:00"))

    details = await current(bilka)

    assert details is not None
    assert details.status is OrderStatus.PACKED
    assert details.pickup_slot is not None
    assert (details.pickup_slot.date, details.pickup_slot.start) == (date(2026, 11, 3), time(16))
    assert details.pickup_point is not None
    assert details.pickup_point.name == "Bilka Eksempelby"
    assert str(details.order_total) == "456.00"
