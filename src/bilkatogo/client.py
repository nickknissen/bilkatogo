import json
from asyncio import sleep
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlencode

import httpx

from .const import (
    ALGOLIA_APP_ID,
    ALGOLIA_PRODUCT_INDEX,
    ALGOLIA_QUERIES_URL,
    ALGOLIA_SEARCH_KEY,
    API_BASE_URL,
    CART_CHANGE_PAUSE_SECONDS,
    CART_CHANGE_RETRY_DELAYS,
    CART_PARAMS,
    CURRENT_ORDER_SCAN_LIMIT,
    GIGYA_API_KEY,
    GIGYA_BASE_URL,
    GIGYA_INVALID_LOGIN_ID,
    JWT_LIFETIME_SECONDS,
    JWT_RENEWAL_MARGIN,
    ORDER_HISTORY_PAGE_SIZE,
    ORDER_HISTORY_PARAMS,
    REQUEST_TIMEOUT_SECONDS,
    SEARCH_LIMIT,
    WEB_PARAMS,
)
from .exceptions import (
    BilkaUnreachableError,
    CartChangeRejectedError,
    InvalidCredentialsError,
    NoPickupPointError,
    OrderNotFoundError,
    UnexpectedResponseError,
    UnknownPickupPointError,
)
from .models import (
    Cart,
    CartChange,
    CartChanges,
    CartChangeStatus,
    Order,
    OrderDetails,
    OrderHistory,
    PickupPoint,
    Product,
    SearchSort,
)
from .models.cart import cart_pickup_point, cart_store_id
from .models.product import sort_products
from .token_storage import TokenStorage


def _order_number(order: str | Order) -> str:
    return order if isinstance(order, str) else order.order_number


def _quantity_in(cart: Cart, article_number: str) -> int:
    return sum(line.quantity for line in cart.lines if line.article_number == article_number)


def _is_error_body(data: dict[str, Any]) -> bool:
    return "code" in data and "msg" in data


def _is_session_expired(data: dict[str, Any]) -> bool:
    """Whether Bilka answered as if logged out.

    Without a valid JWT every shop call still answers HTTP 200, with `uid: -1`: OrderHistory
    `{"code": "ACCESS_DENIED", "msg": "Metoden kræver login", "eid": 401, "uid": -1}`,
    OrderDetails "Ordren kunne ikke findes." and Cart an empty guest Cart (captured
    2026-11-02). `{"code": 1, "msg": "Du er ikke logget ind"}` comes from community code
    and was never captured.
    """
    uid = data.get("uid")
    return (isinstance(uid, int) and uid <= 0) or data.get("code") == 1


class _ShopBusyError(UnexpectedResponseError):
    """Bilka answered a shop call with HTTP 429 or 5xx, which may pass if tried again."""


class _ShopRefusedError(UnexpectedResponseError):
    """Bilka answered a shop call with an error body other than an expired session."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.reason = reason


class BilkaToGoClient:
    def __init__(
        self,
        email: str,
        password: str | None = None,
        *,
        token_storage: TokenStorage | None = None,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._token_storage = token_storage
        self._owns_http = httpx_client is None
        self._http = httpx_client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._jwt: str | None = None
        self._jwt_expires_at: datetime | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def login(self) -> None:
        await self._login()

    async def _login(self) -> str:
        if self._password is None:
            raise InvalidCredentialsError("A password is needed to log in")
        login_token = await self._gigya_login(self._password)
        # The shop accepts Gigya's JWT on its own. The website also calls auth/LoginJWT, which
        # replaces the account's Cart with the one under its `cart` cookie, so we never do.
        jwt = await self._gigya_jwt(login_token)
        expires_at = datetime.now(UTC) + timedelta(seconds=JWT_LIFETIME_SECONDS)
        self._jwt = jwt
        self._jwt_expires_at = expires_at
        if self._token_storage is not None:
            await self._token_storage.save(
                {"email": self._email, "jwt": jwt, "jwt_expires_at": expires_at.isoformat()}
            )
        return jwt

    async def get_orders(self, limit: int = 5, offset: int = 0) -> list[Order]:
        orders: list[Order] = []
        while len(orders) < limit:
            page_size = min(ORDER_HISTORY_PAGE_SIZE, limit - len(orders))
            data = await self._shop(
                "POST",
                "shop/v7/OrderHistory",
                {"limit": page_size, "offset": offset + len(orders), **ORDER_HISTORY_PARAMS},
            )
            page = [Order.from_dict(order) for order in data.get("orders") or []]
            orders.extend(page)
            if len(page) < page_size:
                break
        return orders

    async def get_order(self, order: str | Order) -> OrderDetails:
        order_number = _order_number(order)
        details = (await self.get_order_json(order_number)).get("order")
        if not details:
            raise OrderNotFoundError(order_number)
        summary = await self._find_order(order_number) if isinstance(order, str) else order
        return OrderDetails.from_dict(
            details, ordered_total=summary.ordered_total if summary else None
        )

    async def get_order_history(self) -> OrderHistory:
        return OrderHistory(await self.get_orders(limit=CURRENT_ORDER_SCAN_LIMIT))

    async def get_current_order(self) -> OrderDetails | None:
        order = (await self.get_order_history()).current_order
        return await self.get_order(order) if order else None

    async def get_order_json(self, order: str | Order) -> dict[str, Any]:
        return await self._shop("GET", "shop/v7/OrderDetails", {"orderId": _order_number(order)})

    async def get_current_order_json(self) -> dict[str, Any] | None:
        order = (await self.get_order_history()).current_order
        return await self.get_order_json(order) if order else None

    async def _find_order(self, order_number: str) -> Order | None:
        orders = (await self.get_order_history()).orders
        return next((order for order in orders if order.order_number == order_number), None)

    async def get_pickup_points(self) -> list[PickupPoint]:
        data = await self._shop("GET", "shop/v6/AvailableDeliveryAddresses", {"orderByZipcode": 1})
        return [
            PickupPoint.from_delivery_address(address) for address in data.get("addresses") or []
        ]

    async def get_pickup_point(self, name: str) -> PickupPoint:
        wanted = name.strip().casefold()
        for point in await self.get_pickup_points():
            if point.name.casefold() == wanted:
                return point
        raise UnknownPickupPointError(name)

    async def get_cart(self) -> Cart:
        return Cart.from_dict(await self.get_cart_json())

    async def get_cart_json(self) -> dict[str, Any]:
        return await self._shop("GET", "shop/v6/Cart", CART_PARAMS)

    async def set_cart_quantity(self, article_number: str, quantity: int) -> Cart:
        warnings = await self._change_line_count(article_number, quantity)
        cart = await self.get_cart()
        cart.warnings.extend(warnings)
        return cart

    async def set_cart_quantities(self, quantities: Mapping[str, int]) -> CartChanges:
        """Set the quantity of several Products, one change after another, and read the Cart once.

        A change Bilka rejects is reported and the rest go ahead. When Bilka stops answering,
        even after retries, that change and the rest are skipped and the Cart shows what got
        through; a change that reached Bilka before the answer was lost counts as applied.
        """
        items = list(quantities.items())
        changes: list[CartChange] = []
        lost: CartChange | None = None
        for index, (article_number, quantity) in enumerate(items):
            if index:
                await sleep(CART_CHANGE_PAUSE_SECONDS)
            try:
                warnings = await self._change_line_count_retrying(article_number, quantity)
            except CartChangeRejectedError as rejected:
                changes.append(
                    CartChange(
                        article_number,
                        quantity,
                        rejected.applied,
                        CartChangeStatus.REJECTED,
                        str(rejected),
                    )
                )
                continue
            except (BilkaUnreachableError, _ShopBusyError) as failed:
                lost = CartChange(
                    article_number, quantity, None, CartChangeStatus.SKIPPED, str(failed)
                )
                changes.append(lost)
                changes.extend(
                    CartChange(article, count, None, CartChangeStatus.SKIPPED, None)
                    for article, count in items[index + 1 :]
                )
                break
            message = " ".join(warnings) or None
            changes.append(
                CartChange(article_number, quantity, quantity, CartChangeStatus.APPLIED, message)
            )
        cart = await self.get_cart()
        if lost is not None and _quantity_in(cart, lost.article_number) == lost.requested:
            lost.applied = lost.requested
            lost.status = CartChangeStatus.APPLIED
            lost.message = None
        return CartChanges(changes=changes, cart=cart)

    async def add_items_to_cart(self, items: Mapping[str, int]) -> CartChanges:
        """Add several Products to what the Cart holds, by Article Number and quantity."""
        cart = await self.get_cart()
        return await self.set_cart_quantities(
            {
                article_number: _quantity_in(cart, article_number) + quantity
                for article_number, quantity in items.items()
            }
        )

    async def _change_line_count_retrying(self, article_number: str, quantity: int) -> list[str]:
        for delay in CART_CHANGE_RETRY_DELAYS:
            try:
                return await self._change_line_count(article_number, quantity)
            except BilkaUnreachableError, _ShopBusyError:
                await sleep(delay)
        return await self._change_line_count(article_number, quantity)

    async def _change_line_count(self, article_number: str, quantity: int) -> list[str]:
        """Set one Product's quantity; returns Bilka's warnings."""
        try:
            answer = await self._shop(
                "GET",
                "shop/v6/ChangeLineCount",
                {"productId": article_number, "count": quantity, "fullCart": 0},
            )
        except _ShopRefusedError as refused:
            raise CartChangeRejectedError(
                refused.reason, article_number, quantity, None
            ) from refused
        messages = [
            message
            for message in (answer.get("message"), answer.get("offerLimitMessage"))
            if message
        ]
        applied = answer.get("productAmount")
        if not isinstance(applied, int):
            raise CartChangeRejectedError(
                " ".join(messages) or f"Bilka did not say what quantity of {article_number} it set",
                article_number,
                quantity,
                None,
            )
        if applied != quantity:
            raise CartChangeRejectedError(
                " ".join(messages) or f"Bilka set {article_number} to {applied}, not {quantity}",
                article_number,
                quantity,
                applied,
            )
        return messages

    async def add_to_cart(self, article_number: str, quantity: int = 1) -> Cart:
        current = await self._cart_quantity(article_number)
        return await self.set_cart_quantity(article_number, current + quantity)

    async def remove_from_cart(self, article_number: str, quantity: int | None = None) -> Cart:
        if quantity is None:
            return await self.set_cart_quantity(article_number, 0)
        current = await self._cart_quantity(article_number)
        return await self.set_cart_quantity(article_number, max(current - quantity, 0))

    async def _cart_quantity(self, article_number: str) -> int:
        return _quantity_in(await self.get_cart(), article_number)

    async def clear_cart(self) -> Cart:
        await self._shop("GET", "shop/v6/EmptyCart", CART_PARAMS)
        return await self.get_cart()

    async def get_cart_pickup_point(self) -> PickupPoint | None:
        """The Pickup Point chosen in the Cart; None for Home Delivery or when none is chosen."""
        return cart_pickup_point((await self.get_cart_json()).get("deliveryAddress"))

    async def search(
        self,
        text: str,
        *,
        pickup_point: str | None = None,
        limit: int = SEARCH_LIMIT,
        in_stock: bool = False,
        on_offer: bool = False,
        in_leaflet: bool = False,
        sort: SearchSort = SearchSort.RELEVANCE,
    ) -> list[Product]:
        """Products matching `text` at the Store of a Pickup Point, by default the Store that
        serves the Cart, whether it is collected or brought by Home Delivery.

        `in_stock` keeps Products in stock at the Store, `on_offer` those with an Offer or a
        Multibuy Offer there, `in_leaflet` those in this week's Leaflet. `sort` orders the
        `limit` best matches; it does not search the whole assortment for the cheapest.
        """
        store_id = (
            (await self.get_pickup_point(pickup_point)).store_id
            if pickup_point is not None
            else cart_store_id((await self.get_cart_json()).get("deliveryAddress"))
        )
        if store_id is None:
            raise NoPickupPointError(
                "No Pickup Point or Home Delivery address chosen in the Cart; "
                "name a Pickup Point to search at"
            )
        filters = [f"isInAssortmentIn:{store_id}", "nonsearchable:false"]
        if in_stock:
            filters.append(f"inStockStore:{store_id}")
        if on_offer:
            filters.append(f"isInOffer:{store_id}")
        if in_leaflet:
            filters.append("isInCurrentLeaflet:true")
        params = urlencode(
            {
                "query": text,
                "hitsPerPage": limit,
                "filters": " AND ".join(filters),
                "attributesToRetrieve": json.dumps(
                    ["name", "brand", "price", f"storeData.{store_id}"]
                ),
            }
        )
        response = await self._request(
            "POST",
            ALGOLIA_QUERIES_URL,
            headers={
                "x-algolia-application-id": ALGOLIA_APP_ID,
                "x-algolia-api-key": ALGOLIA_SEARCH_KEY,
            },
            json={"requests": [{"indexName": ALGOLIA_PRODUCT_INDEX, "params": params}]},
        )
        if response.status_code != 200:
            raise UnexpectedResponseError(f"Product search failed with HTTP {response.status_code}")
        try:
            [result] = self._json(response, "Product search")["results"]
            hits = result["hits"]
        except KeyError, TypeError, ValueError:
            raise UnexpectedResponseError("Product search answered without results") from None
        products = (Product.from_hit(hit, store_id) for hit in hits)
        return sort_products([product for product in products if product is not None], sort)

    async def _shop(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        jwt = self._valid_jwt() or await self._restore_session() or await self._login()
        data = await self._shop_request(method, path, params, jwt)
        if data is None or _is_session_expired(data):
            data = await self._shop_request(method, path, params, await self._login())
        if data is None or _is_session_expired(data):
            reason = f": {data['msg']}" if data is not None else ""
            raise UnexpectedResponseError(f"{path} rejected a fresh session{reason}")
        if _is_error_body(data):
            raise _ShopRefusedError(path, data["msg"])
        return data

    async def _shop_request(
        self, method: str, path: str, params: dict[str, Any], jwt: str
    ) -> dict[str, Any] | None:
        response = await self._request(
            method,
            f"{API_BASE_URL}/{path}",
            params={**WEB_PARAMS, **params},
            headers={"jwt_token": jwt},
        )
        if response.status_code == 401:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            raise _ShopBusyError(f"{path} failed with HTTP {response.status_code}")
        if response.status_code != 200:
            raise UnexpectedResponseError(f"{path} failed with HTTP {response.status_code}")
        return self._json(response, path)

    async def _restore_session(self) -> str | None:
        if self._token_storage is None:
            return None
        session = await self._token_storage.load()
        if not session or session.get("email") != self._email:
            return None
        try:
            expires_at = datetime.fromisoformat(session["jwt_expires_at"])
            jwt = session["jwt"]
        except KeyError, TypeError, ValueError:
            return None
        self._jwt = jwt
        self._jwt_expires_at = expires_at
        return self._valid_jwt()

    def _valid_jwt(self) -> str | None:
        if self._jwt_expires_at is None:
            return None
        if self._jwt_expires_at - JWT_RENEWAL_MARGIN <= datetime.now(UTC):
            return None
        return self._jwt

    async def _gigya_login(self, password: str) -> str:
        data = await self._gigya(
            "accounts.login",
            {
                "loginID": self._email,
                "password": password,
                "apiKey": GIGYA_API_KEY,
                "httpStatusCodes": "true",
            },
        )
        session = data.get("sessionInfo")
        login_token = session.get("cookieValue") if isinstance(session, dict) else None
        if not isinstance(login_token, str):
            raise UnexpectedResponseError("Gigya login answered without a login token")
        return login_token

    async def _gigya_jwt(self, login_token: str) -> str:
        data = await self._gigya(
            "accounts.getJWT",
            {
                "apiKey": GIGYA_API_KEY,
                "login_token": login_token,
                "expiration": str(JWT_LIFETIME_SECONDS),
            },
        )
        jwt = data.get("id_token")
        if not isinstance(jwt, str):
            raise UnexpectedResponseError("Gigya answered without a JWT")
        return jwt

    async def _gigya(self, method: str, form: dict[str, str]) -> dict[str, Any]:
        response = await self._request("POST", f"{GIGYA_BASE_URL}/{method}", data=form)
        data = self._json(response, f"Gigya {method}")
        error_code = data.get("errorCode", 0)
        if error_code == GIGYA_INVALID_LOGIN_ID:
            raise InvalidCredentialsError(data.get("errorMessage", "Invalid email or password"))
        if error_code != 0 or response.status_code != 200:
            raise UnexpectedResponseError(
                f"Gigya {method} failed: {data.get('errorMessage', error_code)}"
            )
        return data

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.TransportError as err:
            cause = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
            raise BilkaUnreachableError(f"Could not reach {httpx.URL(url).host}: {cause}") from err

    @staticmethod
    def _json(response: httpx.Response, what: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            raise UnexpectedResponseError(
                f"{what} answered HTTP {response.status_code} without JSON"
            ) from None
        if not isinstance(data, dict):
            raise UnexpectedResponseError(f"{what} answered JSON that is not an object")
        return data
