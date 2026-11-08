from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from freezegun.api import FrozenDateTimeFactory

from bilkatogo import (
    BilkaBusyError,
    BilkaToGoClient,
    BilkaUnreachableError,
    InvalidCredentialsError,
    UnexpectedResponseError,
)
from tests.conftest import MemoryTokenStorage
from tests.fake_bilka import EMAIL, LOGGED_OUT, PASSWORD, FakeBilka, form, load_fixture

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
    assert bilka.calls("login_jwt") == []


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

    assert logged_in_cart.calls("login_jwt") == []
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
async def test_a_jwt_bilka_no_longer_accepts_logs_in_again_on_every_read(
    logged_in_cart: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(logged_in_cart, storage)

    async def revoked[T](read: Callable[[BilkaToGoClient], Awaitable[T]]) -> T:
        assert storage.data is not None
        storage.data["jwt"] = "revoked-by-bilka"
        async with (
            logged_in_cart.client() as http,
            BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
        ):
            return await read(client)

    assert len(await revoked(lambda client: client.get_orders())) == 2
    details = await revoked(lambda client: client.get_order("900000000001"))
    assert details.order_number == "900000000001"
    assert (await revoked(lambda client: client.get_cart())).lines
    assert await revoked(lambda client: client.get_pickup_points())
    assert len(logged_in_cart.calls("gigya_login")) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "logged_out"),
    [
        ("order_history", LOGGED_OUT),
        ("order_history", {"code": 1, "msg": "Du er ikke logget ind"}),
        ("cart", load_fixture("cart.json")),
    ],
)
async def test_a_logged_out_answer_logs_in_again_and_retries_once(
    logged_in_cart: FakeBilka, storage: MemoryTokenStorage, route: str, logged_out: dict
) -> None:
    await log_in(logged_in_cart, storage)
    logged_in_cart.script(route, logged_out)

    async with (
        logged_in_cart.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await client.get_orders() if route == "order_history" else await client.get_cart()

    assert len(logged_in_cart.calls(route)) == 2
    assert len(logged_in_cart.calls("gigya_login")) == 2


@pytest.mark.asyncio
async def test_a_logged_out_answer_after_a_fresh_login_raises_with_bilkas_message(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    bilka.script("order_history", LOGGED_OUT, LOGGED_OUT)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        with pytest.raises(UnexpectedResponseError, match="Metoden kræver login"):
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


LOGGED_IN_AT = [
    datetime(2026, 11, 2, 22, 30, tzinfo=UTC),  # 23:30 in Copenhagen, so it expires a day later
    datetime(2027, 12, 31, 23, 58, tzinfo=UTC),  # expires in the next year
    datetime(2028, 2, 28, 23, 57, tzinfo=UTC),  # expires on a leap day
]
LOGGED_IN_IDS = ["next_day_in_copenhagen", "next_year", "leap_day"]
# Gigya is asked for a JWT that lasts 86400 seconds (test_client); it is renewed 5 minutes early.
JWT_LIFETIME = timedelta(hours=24)


def stored_expiry(storage: MemoryTokenStorage) -> datetime:
    assert storage.data is not None
    return datetime.fromisoformat(storage.data["jwt_expires_at"])


@pytest.mark.asyncio
@pytest.mark.parametrize("logged_in_at", LOGGED_IN_AT, ids=LOGGED_IN_IDS)
async def test_a_login_stores_a_session_that_expires_24_hours_later(
    bilka: FakeBilka,
    storage: MemoryTokenStorage,
    freezer: FrozenDateTimeFactory,
    logged_in_at: datetime,
) -> None:
    freezer.move_to(logged_in_at)

    await log_in(bilka, storage)

    assert stored_expiry(storage) == logged_in_at + JWT_LIFETIME


@pytest.mark.asyncio
@pytest.mark.parametrize("logged_in_at", LOGGED_IN_AT, ids=LOGGED_IN_IDS)
@pytest.mark.parametrize(
    ("used_after", "renews"),
    [
        (JWT_LIFETIME - timedelta(minutes=5, seconds=1), False),
        (JWT_LIFETIME - timedelta(minutes=5), True),
        (JWT_LIFETIME + timedelta(seconds=1), True),
    ],
    ids=["just_before_renewal", "at_renewal", "after_expiry"],
)
async def test_a_stored_session_is_renewed_5_minutes_before_it_expires(
    bilka: FakeBilka,
    storage: MemoryTokenStorage,
    freezer: FrozenDateTimeFactory,
    logged_in_at: datetime,
    used_after: timedelta,
    renews: bool,
) -> None:
    freezer.move_to(logged_in_at)
    await log_in(bilka, storage)
    used_at = logged_in_at + used_after
    freezer.move_to(used_at)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        await client.get_orders()

    # Renewed from the saved login token, without logging in again.
    assert len(bilka.calls("gigya_jwt")) == (2 if renews else 1)
    assert len(bilka.calls("gigya_login")) == 1
    assert len(bilka.calls("order_history")) == 1
    renewed_at = used_at if renews else logged_in_at
    assert stored_expiry(storage) == renewed_at + JWT_LIFETIME


LOGIN_TOKEN = load_fixture("gigya_login.json")["sessionInfo"]["cookieValue"]
REFUSED_TOKEN = httpx.Response(
    403, json={"statusCode": 403, "errorCode": 403005, "errorMessage": "Unauthorized user"}
)


async def expired_session(
    bilka: FakeBilka, storage: MemoryTokenStorage, freezer: FrozenDateTimeFactory
) -> datetime:
    """Log in, then move past the JWT's lifetime; returns the time it is used at."""
    freezer.move_to(LOGGED_IN_AT[0])
    await log_in(bilka, storage)
    used_at = LOGGED_IN_AT[0] + JWT_LIFETIME + timedelta(hours=1)
    freezer.move_to(used_at)
    return used_at


async def orders_without_password(bilka: FakeBilka, storage: MemoryTokenStorage) -> int:
    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, token_storage=storage, httpx_client=http) as client,
    ):
        return len(await client.get_orders())


@pytest.mark.asyncio
async def test_a_login_saves_the_login_token_with_the_jwt(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)

    assert storage.data is not None
    assert storage.data["version"] == 2
    assert storage.data["login_token"] == LOGIN_TOKEN


@pytest.mark.asyncio
async def test_an_expired_jwt_is_renewed_from_the_login_token_without_the_password(
    bilka: FakeBilka, storage: MemoryTokenStorage, freezer: FrozenDateTimeFactory
) -> None:
    used_at = await expired_session(bilka, storage, freezer)

    assert await orders_without_password(bilka, storage) == 2

    [renewal] = bilka.calls("gigya_jwt")[1:]
    assert form(renewal)["login_token"] == LOGIN_TOKEN
    assert len(bilka.calls("gigya_login")) == 1
    assert stored_expiry(storage) == used_at + JWT_LIFETIME
    assert storage.data is not None
    assert storage.data["login_token"] == LOGIN_TOKEN


@pytest.mark.asyncio
async def test_a_refused_login_token_falls_back_to_the_password(
    bilka: FakeBilka, storage: MemoryTokenStorage, freezer: FrozenDateTimeFactory
) -> None:
    used_at = await expired_session(bilka, storage, freezer)
    bilka.script("gigya_jwt", REFUSED_TOKEN)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, token_storage=storage, httpx_client=http) as client,
    ):
        assert len(await client.get_orders()) == 2

    assert len(bilka.calls("gigya_login")) == 2
    assert stored_expiry(storage) == used_at + JWT_LIFETIME
    assert storage.data is not None
    assert storage.data["login_token"] == LOGIN_TOKEN


@pytest.mark.asyncio
async def test_a_refused_login_token_without_a_password_asks_to_log_in_again(
    bilka: FakeBilka, storage: MemoryTokenStorage, freezer: FrozenDateTimeFactory
) -> None:
    await expired_session(bilka, storage, freezer)
    bilka.script("gigya_jwt", REFUSED_TOKEN)

    with pytest.raises(
        InvalidCredentialsError,
        match="The saved session has expired; log in again with your password",
    ):
        await orders_without_password(bilka, storage)

    assert storage.data is not None
    assert "login_token" not in storage.data
    assert bilka.calls("order_history") == []
    assert len(bilka.calls("gigya_login")) == 1


@pytest.mark.asyncio
async def test_an_expired_session_saved_before_login_tokens_needs_the_password(
    bilka: FakeBilka, storage: MemoryTokenStorage, freezer: FrozenDateTimeFactory
) -> None:
    await expired_session(bilka, storage, freezer)
    assert storage.data is not None
    del storage.data["version"], storage.data["login_token"]

    with pytest.raises(InvalidCredentialsError, match="The saved session has expired"):
        await orders_without_password(bilka, storage)

    assert len(bilka.calls("gigya_jwt")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "raised"),
    [
        (httpx.ConnectError("fake: down"), BilkaUnreachableError),
        (httpx.Response(500, json={"errorCode": 500001, "errorMessage": "fake"}), BilkaBusyError),
    ],
    ids=["unreachable", "busy"],
)
async def test_gigya_failing_to_renew_keeps_the_login_token(
    bilka: FakeBilka,
    storage: MemoryTokenStorage,
    freezer: FrozenDateTimeFactory,
    failure: httpx.Response | Exception,
    raised: type[Exception],
) -> None:
    await expired_session(bilka, storage, freezer)
    bilka.script("gigya_jwt", failure)

    with pytest.raises(raised):
        await orders_without_password(bilka, storage)

    assert len(bilka.calls("gigya_login")) == 1
    assert storage.data is not None
    assert storage.data["login_token"] == LOGIN_TOKEN
    assert await orders_without_password(bilka, storage) == 2


@pytest.mark.asyncio
async def test_a_jwt_the_shop_refuses_needs_the_password_even_with_a_login_token(
    bilka: FakeBilka, storage: MemoryTokenStorage
) -> None:
    await log_in(bilka, storage)
    bilka.script("order_history", UNAUTHORIZED)

    with pytest.raises(InvalidCredentialsError, match="The saved session has expired"):
        await orders_without_password(bilka, storage)

    assert len(bilka.calls("gigya_jwt")) == 1


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
            "gigya_jwt",
            httpx.ConnectError("fake: connection reset"),
            "Could not reach accounts.eu1.gigya.com: ConnectError: fake: connection reset",
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
        ("order_history", httpx.Response(200, json=["not", "an", "object"])),
        ("gigya_login", httpx.Response(502, text=HTML_ERROR)),
        ("gigya_login", httpx.Response(200, json={"errorCode": 0})),
        ("gigya_login", httpx.Response(400, json={"errorCode": 400006, "errorMessage": "fake"})),
        ("gigya_jwt", httpx.Response(500, text="Internal Server Error")),
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "response"),
    [
        (
            "gigya_login",
            httpx.Response(403, json={"errorCode": 403048, "errorMessage": "Too many requests"}),
        ),
        ("gigya_login", httpx.Response(500, json={"errorCode": 500028, "errorMessage": "fake"})),
        ("gigya_jwt", httpx.Response(500, json={"errorCode": 500001, "errorMessage": "fake"})),
        ("order_history", httpx.Response(500, json={"error": "fake"})),
        ("order_history", httpx.Response(429, text="Too Many Requests")),
    ],
    ids=[
        "gigya_rate_limit",
        "gigya_server_error",
        "gigya_jwt_server_error",
        "shop_500",
        "shop_429",
    ],
)
async def test_passing_outages_raise_bilka_busy(
    bilka: FakeBilka, route: str, response: httpx.Response
) -> None:
    bilka.script(route, response)

    async with (
        bilka.client() as http,
        BilkaToGoClient(EMAIL, PASSWORD, httpx_client=http) as client,
    ):
        with pytest.raises(BilkaBusyError, match="try again later"):
            await client.get_orders()
