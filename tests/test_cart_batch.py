import httpx
import pytest

from bilkatogo import BilkaToGoClient, CartChanges, CartChangeStatus
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture

APPLIED = CartChangeStatus.APPLIED
REJECTED = CartChangeStatus.REJECTED
SKIPPED = CartChangeStatus.SKIPPED


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("bilkatogo.client.sleep", sleep)
    return slept


async def set_quantities(bilka: FakeBilka, quantities: dict[str, int]) -> CartChanges:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.set_cart_quantities(quantities)


async def add_items(bilka: FakeBilka, items: dict[str, int]) -> CartChanges:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        return await client.add_items_to_cart(items)


def quantities(result: CartChanges) -> dict[str, int]:
    return {line.article_number: line.quantity for line in result.cart.lines}


def outcomes(result: CartChanges) -> list[tuple[str, int, int | None, CartChangeStatus]]:
    return [
        (change.article_number, change.requested, change.applied, change.status)
        for change in result.changes
    ]


@pytest.mark.asyncio
async def test_set_cart_quantities_changes_each_in_order_and_reads_the_cart_once(
    logged_in_cart: FakeBilka, sleeps: list[float]
) -> None:
    result = await set_quantities(logged_in_cart, {"18381": 4, "19753": 2, "18364": 0})

    counts = [
        (request.url.params["productId"], request.url.params["count"])
        for request in logged_in_cart.calls("change_line_count")
    ]
    assert counts == [("18381", "4"), ("19753", "2"), ("18364", "0")]
    assert len(logged_in_cart.calls("cart")) == 1
    assert outcomes(result) == [
        ("18381", 4, 4, APPLIED),
        ("19753", 2, 2, APPLIED),
        ("18364", 0, 0, APPLIED),
    ]
    assert all(change.message is None for change in result.changes)
    assert quantities(result)["18381"] == 4
    assert quantities(result)["19753"] == 2
    assert "18364" not in quantities(result)
    assert sleeps == [0.25, 0.25]


@pytest.mark.asyncio
async def test_add_items_to_cart_adds_to_what_the_cart_holds(
    logged_in_cart: FakeBilka, sleeps: list[float]
) -> None:
    result = await add_items(logged_in_cart, {"18381": 2, "19753": 1})

    assert outcomes(result) == [("18381", 12, 12, APPLIED), ("19753", 1, 1, APPLIED)]
    assert len(logged_in_cart.calls("cart")) == 2
    assert quantities(result)["18381"] == 12
    assert quantities(result)["19753"] == 1


@pytest.mark.asyncio
async def test_no_items_only_reads_the_cart(logged_in_cart: FakeBilka) -> None:
    result = await set_quantities(logged_in_cart, {})

    assert result.changes == []
    assert logged_in_cart.calls("change_line_count") == []
    assert result.cart.lines


@pytest.mark.asyncio
async def test_a_rejected_change_is_reported_and_the_rest_go_ahead(
    logged_in_cart: FakeBilka, sleeps: list[float]
) -> None:
    logged_in_cart.script("change_line_count", load_fixture("change_line_count_unavailable.json"))

    result = await set_quantities(logged_in_cart, {"88672": 1, "19753": 2})

    assert outcomes(result) == [("88672", 1, None, REJECTED), ("19753", 2, 2, APPLIED)]
    assert result.changes[0].message == (
        "Produktet Tubetti m. boghvede glutenfri øko (ID: 88672) er desværre ikke aktuelt "
        "tilgængeligt"
    )
    assert quantities(result)["19753"] == 2


@pytest.mark.asyncio
async def test_a_change_bilka_caps_is_rejected_with_the_quantity_it_set(
    logged_in_cart: FakeBilka, sleeps: list[float]
) -> None:
    logged_in_cart.max_quantity = 2
    logged_in_cart.change_answer = {
        **load_fixture("change_line_count.json"),
        "offerLimitMessage": "Maks. 2 pr. kunde",
    }

    result = await set_quantities(logged_in_cart, {"18364": 5})

    assert outcomes(result) == [("18364", 5, 2, REJECTED)]
    assert result.changes[0].message == "Maks. 2 pr. kunde"


@pytest.mark.asyncio
async def test_a_warning_on_an_applied_change_is_its_message(
    logged_in_cart: FakeBilka, sleeps: list[float]
) -> None:
    logged_in_cart.change_answer = load_fixture("change_line_count_warning.json")

    result = await set_quantities(logged_in_cart, {"18364": 99})

    assert outcomes(result) == [("18364", 99, 99, APPLIED)]
    assert result.changes[0].message is not None
    assert result.changes[0].message.startswith("Ring venligst til kundeservice")


@pytest.mark.asyncio
async def test_a_change_bilka_does_not_answer_is_retried_with_backoff(
    logged_in_cart: FakeBilka, sleeps: list[float]
) -> None:
    logged_in_cart.script(
        "change_line_count",
        httpx.Response(503, text="Service Unavailable"),
        httpx.ReadTimeout("fake: timed out"),
        httpx.Response(429, text="Too Many Requests"),
    )

    result = await set_quantities(logged_in_cart, {"18364": 3})

    assert outcomes(result) == [("18364", 3, 3, APPLIED)]
    assert len(logged_in_cart.calls("change_line_count")) == 4
    assert sleeps == [0.5, 1.0, 2.0]


@pytest.mark.asyncio
async def test_when_bilka_stops_answering_the_rest_are_skipped_and_the_cart_is_returned(
    logged_in_cart: FakeBilka, sleeps: list[float]
) -> None:
    down = httpx.ConnectError("fake: connection refused")
    logged_in_cart.script(
        "change_line_count", logged_in_cart.change_line_count, down, down, down, down
    )

    result = await set_quantities(logged_in_cart, {"18381": 4, "18364": 3, "19753": 1})

    assert outcomes(result) == [
        ("18381", 4, 4, APPLIED),
        ("18364", 3, None, SKIPPED),
        ("19753", 1, None, SKIPPED),
    ]
    assert result.changes[1].message == (
        "Could not reach api.bilkatogo.dk: ConnectError: fake: connection refused"
    )
    assert result.changes[2].message is None
    assert len(logged_in_cart.calls("change_line_count")) == 5
    assert quantities(result)["18364"] == 1


@pytest.mark.asyncio
async def test_a_change_that_got_through_before_bilka_stopped_answering_counts_as_applied(
    logged_in_cart: FakeBilka, sleeps: list[float]
) -> None:
    def applied_then_lost(request: httpx.Request) -> httpx.Response:
        logged_in_cart.change_line_count(request)
        raise httpx.ReadTimeout("fake: answer lost")

    logged_in_cart.script("change_line_count", *[applied_then_lost] * 4)

    result = await set_quantities(logged_in_cart, {"18364": 3, "19753": 1})

    assert outcomes(result) == [("18364", 3, 3, APPLIED), ("19753", 1, None, SKIPPED)]
    assert result.changes[0].message is None
    assert quantities(result)["18364"] == 3
