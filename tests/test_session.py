from datetime import UTC, datetime, timedelta

import httpx
import pytest

from bilkatogo import BilkaToGoClient, BilkaUnreachableError, UnexpectedResponseError
from tests.conftest import MemoryTokenStorage
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka

UNAUTHORIZED = httpx.Response(401, json={"code": 401, "msg": "Unauthorized"})


async def log_in(bilka: FakeBilka, storage: MemoryTokenStorage) -> None:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await client.login()


@pytest.mark.asyncio
async def test_unauthorized_shop_call_logs_in_again_and_retries_once(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    bilka.script("order_history", UNAUTHORIZED)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        orders = await client.get_orders()

    assert len(orders) == 2
    assert len(bilka.calls("order_history")) == 2
    assert len(bilka.calls("gigya_login")) == 2
    assert len(bilka.calls("login_jwt")) == 2


@pytest.mark.asyncio
async def test_logging_in_again_keeps_cart_changes_made_elsewhere(
    logged_in_cart: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(logged_in_cart, storage)
    async with (
        logged_in_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as website,
    ):
        await website.set_cart_quantity("18364", 3)
    logged_in_cart.script("cart", UNAUTHORIZED)

    async with (
        logged_in_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        cart = await client.get_cart()

    assert len(logged_in_cart.calls("login_jwt")) == 3
    assert {line.article_number: line.quantity for line in cart.lines}["18364"] == 3


@pytest.mark.asyncio
async def test_unauthorized_order_details_logs_in_again_and_retries_once(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    bilka.script("order_details", UNAUTHORIZED)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        details = await client.get_order("900000000001")

    assert details.order_number == "900000000001"
    assert len(bilka.calls("order_details")) == 2
    assert len(bilka.calls("gigya_login")) == 2


@pytest.mark.asyncio
async def test_unauthorized_again_after_a_fresh_login_raises_instead_of_looping(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    bilka.script("order_history", UNAUTHORIZED, UNAUTHORIZED, UNAUTHORIZED)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        with pytest.raises(UnexpectedResponseError):
            await client.get_orders()

    assert len(bilka.calls("order_history")) == 2
    assert len(bilka.calls("gigya_login")) == 2


@pytest.mark.asyncio
async def test_ok_answer_with_an_error_body_counts_as_an_expired_session(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    bilka.script("order_history", {"code": 1, "msg": "Du er ikke logget ind"})

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        orders = await client.get_orders()

    assert len(orders) == 2
    assert len(bilka.calls("gigya_login")) == 2


@pytest.mark.asyncio
async def test_an_error_body_after_a_fresh_login_raises_with_bilkas_message(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    logged_out = {"code": 1, "msg": "Du er ikke logget ind"}
    bilka.script("order_history", logged_out, logged_out)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        with pytest.raises(UnexpectedResponseError, match="Du er ikke logget ind"):
            await client.get_orders()

    assert len(bilka.calls("order_history")) == 2
    assert len(bilka.calls("gigya_login")) == 2


@pytest.mark.asyncio
async def test_an_error_body_that_is_not_an_expired_session_raises_without_logging_in_again(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    bilka.script("order_history", {"msg": "Noget gik galt", "code": None, "eid": 410})

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        with pytest.raises(UnexpectedResponseError, match="Noget gik galt"):
            await client.get_orders()

    assert len(bilka.calls("order_history")) == 1
    assert len(bilka.calls("gigya_login")) == 1


@pytest.mark.asyncio
async def test_stored_session_about_to_expire_is_renewed_before_the_shop_call(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    assert storage.data is not None
    soon = datetime.now(UTC) + timedelta(minutes=2)
    storage.data["jwt_expires_at"] = soon.isoformat()

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await client.get_orders()

    assert len(bilka.calls("gigya_login")) == 2
    assert len(bilka.calls("order_history")) == 1
    assert datetime.fromisoformat(storage.data["jwt_expires_at"]) > soon + timedelta(hours=23)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "failure", "message"),
    [
        (
            "order_history",
            httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
            "Could not reach api.bilkatogo.dk: "
            "ConnectError: [Errno -3] Temporary failure in name resolution",
        ),
        (
            "order_history",
            httpx.ReadTimeout(""),
            "Could not reach api.bilkatogo.dk: ReadTimeout",
        ),
        (
            "gigya_login",
            httpx.ConnectTimeout("fake: connect timed out"),
            "Could not reach accounts.eu1.gigya.com: ConnectTimeout: fake: connect timed out",
        ),
        (
            "login_jwt",
            httpx.ConnectError("fake: connection reset"),
            "Could not reach api.bilkatogo.dk: ConnectError: fake: connection reset",
        ),
    ],
)
async def test_connection_failures_raise_bilka_unreachable_naming_the_cause(
    bilka: FakeBilka, route: str, failure: Exception, message: str
) -> None:
    bilka.script(route, failure)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        with pytest.raises(BilkaUnreachableError) as raised:
            await client.get_orders()

    assert str(raised.value) == message


HTML_ERROR = "<html><body>Bad Gateway</body></html>"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "response"),
    [
        ("order_history", httpx.Response(200, text=HTML_ERROR)),
        ("order_history", httpx.Response(500, json={"error": "fake"})),
        ("order_history", httpx.Response(200, json=["not", "an", "object"])),
        ("gigya_login", httpx.Response(502, text=HTML_ERROR)),
        ("gigya_login", httpx.Response(200, json={"errorCode": 0})),
        ("gigya_jwt", httpx.Response(500, json={"errorCode": 500001, "errorMessage": "fake"})),
        ("login_jwt", httpx.Response(500, text="Internal Server Error")),
    ],
)
async def test_unexpected_answers_raise_unexpected_response(
    bilka: FakeBilka, route: str, response: httpx.Response
) -> None:
    bilka.script(route, response)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        with pytest.raises(UnexpectedResponseError):
            await client.get_orders()
