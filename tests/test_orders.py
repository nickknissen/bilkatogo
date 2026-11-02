from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from bilkatogo import BilkaToGoClient, InvalidCredentialsError, Order, OrderStatus
from tests.conftest import MemoryTokenStorage
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture

COPENHAGEN = ZoneInfo("Europe/Copenhagen")


async def fetch_one(bilka: FakeBilka, order: dict) -> Order:
    bilka.orders = [order]
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        [fetched] = await client.get_orders()
    return fetched


def synthetic_order(**overrides) -> dict:
    return {**load_fixture("order_history.json")["orders"][0], **overrides}


async def log_in(bilka: FakeBilka, storage: MemoryTokenStorage) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await client.login()


@pytest.mark.asyncio
async def test_stored_session_is_used_without_a_password(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, token_storage=storage, httpx_client=http) as client,
    ):
        orders = await client.get_orders()

    assert len(orders) == 2
    assert len(bilka.calls("gigya_login")) == 1


@pytest.mark.asyncio
async def test_expired_stored_session_logs_in_again(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    assert storage.data is not None
    storage.data["jwt_expires_at"] = "2020-01-01T00:00:00+00:00"

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await client.get_orders()

    assert len(bilka.calls("gigya_login")) == 2
    assert storage.data["jwt_expires_at"] != "2020-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_stored_session_of_another_account_is_not_used(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)

    async with (
        bilka.client() as http,
        BilkaToGoClient("anden@example.com", token_storage=storage, httpx_client=http) as client,
    ):
        with pytest.raises(InvalidCredentialsError):
            await client.get_orders()

    assert bilka.calls("order_history") == []


@pytest.mark.asyncio
async def test_login_needed_without_a_password_raises_invalid_credentials(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, token_storage=storage, httpx_client=http) as client,
    ):
        with pytest.raises(InvalidCredentialsError):
            await client.get_orders()

    assert bilka.requests == []


@pytest.mark.asyncio
async def test_get_orders_logs_in_and_returns_typed_orders(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        orders = await client.get_orders()

    [request] = bilka.calls("order_history")
    assert request.content == b""
    assert dict(request.url.params) == {
        "u": "w",
        "limit": "5",
        "offset": "0",
        "muah": "1",
        "ordertype": "dibs,pgw",
    }

    upcoming, collected = orders
    assert upcoming.order_number == "900000000002"
    assert upcoming.status is OrderStatus.RECEIVED
    assert upcoming.status_code == "Bestilt"
    assert upcoming.ordered_at == datetime(2026, 11, 1, 20, 15, tzinfo=COPENHAGEN)
    assert upcoming.pickup_slot is not None
    assert upcoming.pickup_slot.date == date(2026, 11, 3)
    assert upcoming.pickup_slot.start == time(16, 0)
    assert upcoming.pickup_slot.end == time(18, 0)
    assert upcoming.pickup_point is not None
    assert upcoming.pickup_point.name == "Bilka Eksempelby"
    assert upcoming.pickup_point.address == "Testvej 1"
    assert upcoming.pickup_point.zip_code == "9999"
    assert upcoming.pickup_point.city == "Eksempelby"
    assert upcoming.home_delivery is False
    assert upcoming.ordered_total == Decimal("456.00")
    assert upcoming.order_total == Decimal("449.00")
    assert upcoming.content == "Æbler, Havregryn, Letmælk"

    assert collected.order_number == "900000000001"
    assert collected.status is OrderStatus.PICKED_UP
    assert collected.delivered is True
    assert collected.order_total == Decimal("509.00")
    assert str(collected.ordered_total) == "512.00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "expected", "pages"),
    [(21, 21, [("20", "0"), ("1", "20")]), (40, 23, [("20", "0"), ("20", "20")])],
)
async def test_limits_above_twenty_are_fetched_in_pages(
    bilka: FakeBilka, limit: int, expected: int, pages: list[tuple[str, str]]
) -> None:
    bilka.orders = [synthetic_order(orderId=f"9000000001{n:02d}") for n in range(23)]

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        orders = await client.get_orders(limit=limit)

    assert [o.order_number for o in orders] == [f"9000000001{n:02d}" for n in range(expected)]
    assert [
        (r.url.params["limit"], r.url.params["offset"]) for r in bilka.calls("order_history")
    ] == pages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("Ubehandlet", OrderStatus.PROCESSING),
        ("Capture igang", OrderStatus.PROCESSING),
        ("Bestilt", OrderStatus.RECEIVED),
        ("Pakket", OrderStatus.PACKED),
        ("Leveret", OrderStatus.PICKED_UP),
        ("Faktureret", OrderStatus.PICKED_UP),
        ("Uafhentet", OrderStatus.NOT_COLLECTED),
        ("Annulleret", OrderStatus.CANCELLED),
        ("Fuldt udsolgt", OrderStatus.SOLD_OUT),
        ("  capture IGANG ", OrderStatus.PROCESSING),
        ("FULDT_UDSOLGT", OrderStatus.SOLD_OUT),
        ("Ny ukendt status", OrderStatus.UNKNOWN),
        ("", OrderStatus.UNKNOWN),
    ],
)
async def test_bilka_status_code_maps_to_order_status_and_is_kept(
    bilka: FakeBilka, code: str, status: OrderStatus
) -> None:
    order = await fetch_one(bilka, synthetic_order(status=code))

    assert order.status is status
    assert order.status_code == code


@pytest.mark.asyncio
async def test_unknown_keys_are_tolerated_and_kept_in_raw(bilka: FakeBilka) -> None:
    raw = synthetic_order(organisation={"id": 1}, deliveryMessage="Ring på")

    order = await fetch_one(bilka, raw)

    assert order._raw == raw
    assert "organisation" not in dict(order)


@pytest.mark.asyncio
async def test_home_delivery_flag_is_on_the_order(bilka: FakeBilka) -> None:
    raw = synthetic_order()
    raw["pickupzone"] = {**raw["pickupzone"], "home_delivery": True}

    order = await fetch_one(bilka, raw)

    assert order.home_delivery is True


@pytest.mark.asyncio
async def test_order_with_missing_optional_fields_still_parses(bilka: FakeBilka) -> None:
    order = await fetch_one(bilka, {"orderId": "900000000003", "status": "Pakket"})

    assert order.order_number == "900000000003"
    assert order.status is OrderStatus.PACKED
    assert order.pickup_slot is None
    assert order.pickup_point is None
    assert order.ordered_at is None
    assert order.home_delivery is False
    assert order.ordered_total == Decimal("0.00")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delivery_date", "starts_at", "ends_at"),
    [
        ("2026-11-03", "2026-11-03T16:00:00+01:00", "2026-11-03T18:00:00+01:00"),
        ("2026-07-01", "2026-07-01T16:00:00+02:00", "2026-07-01T18:00:00+02:00"),
    ],
    ids=["winter", "summer"],
)
async def test_pickup_slot_times_are_in_copenhagen(
    bilka: FakeBilka, delivery_date: str, starts_at: str, ends_at: str
) -> None:
    order = await fetch_one(bilka, synthetic_order(deliveryDate=delivery_date))

    assert order.pickup_slot is not None
    assert order.pickup_slot.starts_at.isoformat() == starts_at
    assert order.pickup_slot.ends_at.isoformat() == ends_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "finished"),
    [
        ("Ubehandlet", False),
        ("Bestilt", False),
        ("Pakket", False),
        ("Faktureret", True),
        ("Uafhentet", True),
        ("Annulleret", True),
        ("Fuldt udsolgt", True),
        ("Noget nyt", False),
    ],
)
async def test_an_order_is_finished_once_nothing_more_will_happen_to_it(
    bilka: FakeBilka, status_code: str, finished: bool
) -> None:
    order = await fetch_one(bilka, synthetic_order(status=status_code))

    assert order.finished is finished
