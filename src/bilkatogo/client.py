import json
from asyncio import sleep
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, NamedTuple, Self
from urllib.parse import urlencode

import httpx

from .const import (
    ALGOLIA_APP_ID,
    ALGOLIA_PRODUCT_INDEX,
    ALGOLIA_QUERIES_URL,
    ALGOLIA_SEARCH_KEY,
    API_BASE_URL,
    BILKA_TIME_ZONE,
    CART_CHANGE_PAUSE_SECONDS,
    CART_CHANGE_RETRY_DELAYS,
    CART_PARAMS,
    CHOOSABLE_SLOT_DAYS,
    CURRENT_ORDER_SCAN_LIMIT,
    GIGYA_API_KEY,
    GIGYA_BASE_URL,
    GIGYA_INVALID_LOGIN_ID,
    GIGYA_RATE_LIMIT,
    GIGYA_SERVER_ERRORS,
    JWT_LIFETIME_SECONDS,
    JWT_RENEWAL_MARGIN,
    ORDER_HISTORY_PAGE_SIZE,
    ORDER_HISTORY_PARAMS,
    PRODUCT_LOOKUP_LIMIT,
    REQUEST_TIMEOUT_SECONDS,
    SEARCH_LIMIT,
    SESSION_VERSION,
    UNKNOWN_PLACE_EID,
    WEB_PARAMS,
)
from .exceptions import (
    BilkaBusyError,
    BilkaToGoError,
    BilkaUnreachableError,
    CartChangeRejectedError,
    InvalidCredentialsError,
    NoHomeAddressError,
    NoPickupPointError,
    OrderNotFoundError,
    PickupSlotUnavailableError,
    UnexpectedResponseError,
    UnknownPickupPointError,
)
from .models import (
    Cart,
    CartChange,
    CartChanges,
    CartChangeStatus,
    ChosenPickupSlot,
    DeliveryChange,
    HomeAddress,
    Order,
    OrderDetails,
    OrderHistory,
    PickupPoint,
    PickupSlot,
    PickupSlots,
    Product,
    RecipeDetails,
    SearchSort,
    SlotAvailability,
)
from .models.cart import cart_pickup_point, cart_store_id, is_home_delivery, is_pickup_point
from .models.product import blockbit, product_attributes, sort_products
from .token_storage import TokenStorage


def _order_number(order: str | Order) -> str:
    return order if isinstance(order, str) else order.order_number


def _quantity_in(cart: Cart, article_number: str) -> int:
    return sum(line.quantity for line in cart.lines if line.article_number == article_number)


def _same_slot(one: PickupSlot, other: PickupSlot) -> bool:
    """Whether two Pickup Slots are the same date and times, wherever they were read from."""
    return (one.date, one.start, one.end) == (other.date, other.start, other.end)


def _check_slot_chosen(answer: dict[str, Any], slot: PickupSlot) -> None:
    """Raise unless SetDeliveryDate's answer confirms `slot`: `eid: -1` and the slot in its
    `deliveryDate`, `intervalStart` and `intervalEnd`."""
    if answer.get("eid") != -1:
        if message := answer.get("msg"):
            raise PickupSlotUnavailableError(slot, str(message), refused=True)
        raise UnexpectedResponseError("SetDeliveryDate answered without eid -1")
    chosen = PickupSlot.from_cart(answer)
    if chosen is None:
        raise UnexpectedResponseError("SetDeliveryDate answered without a Pickup Slot")
    if not _same_slot(chosen, slot):
        raise UnexpectedResponseError(f"SetDeliveryDate answered with {chosen}, not {slot}")


def _saved_home_address(session: dict[str, Any]) -> HomeAddress | None:
    """The Home Address in a saved session; None when it has none or not as this release
    saves it."""
    saved = session.get("home_address")
    if not isinstance(saved, dict):
        return None
    place_id, zip_code = saved.get("id"), saved.get("zip_code")
    if not (isinstance(place_id, str) and place_id and isinstance(zip_code, str) and zip_code):
        return None
    return HomeAddress(id=place_id, zip_code=zip_code)


def _place_id(cart: dict[str, Any]) -> str | None:
    """Bilka's id of the place the Cart is received at: its Pickup Point or Home Address."""
    place_id = (cart.get("deliveryAddress") or {}).get("id")
    return str(place_id) if place_id else None


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


class _ShopBusyError(BilkaBusyError):
    """Bilka answered a shop call with HTTP 429 or 5xx, which may pass if tried again."""


class _GigyaRefusedError(UnexpectedResponseError):
    """Gigya answered with an `errorCode` other than a wrong password, a rate limit or its own
    server error, e.g. 403005 for a login token it no longer accepts."""


class _ShopRefusedError(UnexpectedResponseError):
    """Bilka answered a shop call with an error body other than an expired session."""

    def __init__(self, path: str, reason: str, eid: Any = None) -> None:
        super().__init__(f"{path}: {reason}")
        self.reason = reason
        self.eid = eid


class _ProductPlace(NamedTuple):
    """Where Products are looked up for: the Store whose assortment, Prices and stock they
    have, and the place's `blockbit`, which marks those under a Delivery Block."""

    store_id: str
    blockbit: int


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
        self._login_token: str | None = None
        # The last Home Address seen, by id and zip code only (ADR 0009).
        self._home_address: HomeAddress | None = None

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
            # `_jwt` is set once a session was restored, even an expired one, or started.
            if self._jwt is not None:
                raise InvalidCredentialsError(
                    "The saved session has expired; log in again with your password"
                )
            raise InvalidCredentialsError("A password is needed to log in")
        login_token = await self._gigya_login(self._password)
        # The shop accepts Gigya's JWT on its own. The website also calls auth/LoginJWT, which
        # replaces the account's Cart with the one under its `cart` cookie, so we never do.
        jwt = await self._gigya_jwt(login_token)
        self._login_token = login_token
        await self._start_session(jwt)
        return jwt

    async def _renew_jwt(self) -> str | None:
        """A fresh JWT from the saved login token, without the password (ADR 0002).

        None when there is no token or Gigya refuses it; a refused token is dropped from the
        saved session. When Gigya is busy or unreachable that is raised: the token may still
        be good.
        """
        if self._login_token is None:
            return None
        try:
            jwt = await self._gigya_jwt(self._login_token)
        except (_GigyaRefusedError, InvalidCredentialsError):
            self._login_token = None
            await self._save_session()
            return None
        await self._start_session(jwt)
        return jwt

    async def _start_session(self, jwt: str) -> None:
        self._jwt = jwt
        self._jwt_expires_at = datetime.now(UTC) + timedelta(seconds=JWT_LIFETIME_SECONDS)
        await self._save_session()

    async def _save_session(self) -> None:
        if self._token_storage is None or self._jwt_expires_at is None:
            return
        if self._home_address is None and (saved := await self._saved_session()):
            # A login that did not restore the session first keeps what it remembered.
            self._home_address = _saved_home_address(saved)
        session: dict[str, Any] = {
            "version": SESSION_VERSION,
            "email": self._email,
            "jwt": self._jwt,
            "jwt_expires_at": self._jwt_expires_at.isoformat(),
        }
        if self._login_token is not None:
            session["login_token"] = self._login_token
        if self._home_address is not None:
            # An optional key, so no new SESSION_VERSION: older releases ignore it.
            session["home_address"] = {
                "id": self._home_address.id,
                "zip_code": self._home_address.zip_code,
            }
        await self._token_storage.save(session)

    async def _saved_session(self) -> dict[str, Any] | None:
        """The saved session, when it is this account's and in a format this release knows."""
        if self._token_storage is None:
            return None
        session = await self._token_storage.load()
        if not session or session.get("email") != self._email:
            return None
        version = session.get("version", 1)
        if not isinstance(version, int) or version > SESSION_VERSION:
            # Saved by a newer release in a format this one does not know.
            return None
        return session

    async def _remember_home_address(self, places: Iterable[Any]) -> None:
        """Remember the Home Address among `places` read from Bilka, saving it with the session
        when it is new. Bilka lists it only while the Cart is on Home Delivery."""
        for place in places:
            if not isinstance(place, dict) or not is_home_delivery(place):
                continue
            seen = HomeAddress.from_delivery_address(place)
            if not (seen.id and seen.zip_code):
                continue
            remembered = HomeAddress(id=seen.id, zip_code=seen.zip_code)
            if remembered != self._home_address:
                self._home_address = remembered
                await self._save_session()
            return

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
        """The Pickup Points the Cart can be moved to; never the Home Address, which Bilka
        lists among them while the Cart is on Home Delivery."""
        return [
            PickupPoint.from_delivery_address(address)
            for address in await self._pickup_point_addresses()
        ]

    async def get_pickup_point(self, name: str) -> PickupPoint:
        return PickupPoint.from_delivery_address(await self._pickup_point_address(name))

    async def _pickup_point_addresses(self) -> list[dict[str, Any]]:
        data = await self._shop("GET", "shop/v6/AvailableDeliveryAddresses", {"orderByZipcode": 1})
        addresses = [
            address for address in data.get("addresses") or [] if isinstance(address, dict)
        ]
        await self._remember_home_address([data.get("activeAddress"), *addresses])
        return [address for address in addresses if is_pickup_point(address)]

    async def _pickup_point_address(self, name: str) -> dict[str, Any]:
        wanted = name.strip().casefold()
        for address in await self._pickup_point_addresses():
            if PickupPoint.from_delivery_address(address).name.casefold() == wanted:
                return address
        raise UnknownPickupPointError(name)

    async def get_cart(self) -> Cart:
        return Cart.from_dict(await self.get_cart_json())

    async def get_cart_json(self) -> dict[str, Any]:
        data = await self._shop("GET", "shop/v6/Cart", CART_PARAMS)
        await self._remember_home_address([data.get("deliveryAddress")])
        return data

    async def get_home_address(self) -> HomeAddress | None:
        """The Home Address Home Delivery goes to: the Cart's while it is on Home Delivery,
        otherwise the last one the client has seen, which has only its id and zip code. None
        until Home Delivery was chosen once on bilkatogo.dk and the client read the Cart or
        `get_pickup_points` since."""
        return self._home_address_of(await self.get_cart_json())

    def _home_address_of(self, cart: dict[str, Any]) -> HomeAddress | None:
        address = cart.get("deliveryAddress")
        if isinstance(address, dict) and is_home_delivery(address):
            return HomeAddress.from_delivery_address(address)
        return self._home_address

    async def choose_pickup_point(self, name: str) -> DeliveryChange:
        """Move the Cart to the Pickup Point with this name (ADR 0009), and report what that
        changed in it. Its Store then serves the Cart: Prices, Offers and stock may change, and
        Bilka marks the Cart Lines it can't supply unavailable. Nothing is sent when the Cart is
        there already."""
        point = await self.get_pickup_point(name)
        if point.id is None:
            raise UnexpectedResponseError(f"Bilka lists {point.name} without an id")
        return await self._move_cart(
            point.id,
            point.zip_code,
            lambda reason: UnknownPickupPointError(point.name, reason),
            await self.get_cart_json(),
        )

    async def choose_home_delivery(self) -> DeliveryChange:
        """Move the Cart to Home Delivery at the Home Address `get_home_address` gives (ADR
        0009), and report what that changed in it. Home Delivery has a Delivery Fee and a
        Minimum Order. Nothing is sent when the Cart is on Home Delivery already."""
        cart = await self.get_cart_json()
        home = self._home_address_of(cart)
        if home is None:
            raise NoHomeAddressError()
        return await self._move_cart(home.id, home.zip_code, NoHomeAddressError, cart)

    async def _move_cart(
        self,
        place_id: str,
        zip_code: str,
        refused: Callable[[str], BilkaToGoError],
        before: dict[str, Any],
    ) -> DeliveryChange:
        """Send SetDeliveryAddress for the place, read the Cart again and check it moved. The
        client sends only the id and zip code, never an address of its own (ADR 0009)."""
        if _place_id(before) == place_id:
            cart = Cart.from_dict(before)
            return DeliveryChange.between(cart, cart, changed=False)
        try:
            answer = await self._shop(
                "GET", "shop/v6/SetDeliveryAddress", {"id": place_id, "zipcode": zip_code}
            )
        except _ShopRefusedError as err:
            # A place Bilka does not know: `eid` 406, "Udleveringsstedet kunne ikke findes", and
            # the Cart stays where it was (captured 2026-11-09). Any other refusal, e.g. the
            # website's BLOCKED_PRODUCTS (428, never seen), is raised with Bilka's message.
            if err.eid == UNKNOWN_PLACE_EID:
                raise refused(err.reason) from err
            raise
        message = f": {answer['msg']}" if answer.get("msg") else ""
        if answer.get("eid") != -1:
            raise UnexpectedResponseError(f"SetDeliveryAddress answered without eid -1{message}")
        after = await self.get_cart_json()
        if _place_id(after) != place_id:
            raise UnexpectedResponseError(
                f"SetDeliveryAddress answered, but the Cart did not move to {place_id}{message}"
            )
        return DeliveryChange.between(Cart.from_dict(before), Cart.from_dict(after))

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
            except (BilkaUnreachableError, _ShopBusyError):
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

    async def get_pickup_slots(self, *, days: int = 7) -> PickupSlots:
        """The Pickup Slots of the next `days` days, today included, with their availability,
        for what the Cart has: its Pickup Point or Home Delivery. `choose_pickup_slot` chooses
        one."""
        if days < 1:
            raise ValueError("days must be 1 or more")
        cart = await self.get_cart()
        today = datetime.now(BILKA_TIME_ZONE).date()
        return PickupSlots.from_dict(
            await self.get_pickup_slots_json(),
            cart,
            first_day=today,
            last_day=today + timedelta(days=days - 1),
        )

    async def get_pickup_slots_json(self) -> dict[str, Any]:
        return await self._shop("GET", "shop/v3/AvailableDeliveryDates", {})

    async def choose_pickup_slot(self, slot: PickupSlot) -> ChosenPickupSlot:
        """Choose the Cart's Pickup Slot among the open ones `get_pickup_slots` lists for
        the next 14 days (ADR 0008). Choosing holds nothing: the slot can still sell out
        until the Order is placed on bilkatogo.dk. The slot already chosen is returned as it
        is, without asking Bilka, even when it has sold out since."""
        slots = await self.get_pickup_slots(days=CHOOSABLE_SLOT_DAYS)
        if slots.chosen is not None and _same_slot(slots.chosen, slot):
            return ChosenPickupSlot(chosen=slots.chosen, replaced=slots.chosen, changed=False)
        listed = next((offered for offered in slots.slots if _same_slot(offered.slot, slot)), None)
        if listed is None:
            raise PickupSlotUnavailableError(
                slot,
                f"Bilka does not offer it for the Cart in the next {CHOOSABLE_SLOT_DAYS} days",
            )
        if listed.availability is not SlotAvailability.OPEN:
            raise PickupSlotUnavailableError(
                slot,
                f"it is {listed.availability.value.replace('_', ' ')}",
                listed.availability,
            )
        try:
            answer = await self._shop(
                "GET",
                "shop/v3/SetDeliveryDate",
                {
                    "deliveryDate": slot.date.isoformat(),
                    "intervalStart": f"{slot.start:%H:%M}",
                    "intervalEnd": f"{slot.end:%H:%M}",
                },
            )
        except _ShopRefusedError as refused:
            # A slot that closed or sold out since it was listed: `eid` 406, "Det valgte
            # tidspunkt er ikke tilgængelig", and the Cart keeps its slot (captured 2026-11-09).
            raise PickupSlotUnavailableError(slot, refused.reason, refused=True) from refused
        _check_slot_chosen(answer, slot)
        return ChosenPickupSlot(chosen=listed.slot, replaced=slots.chosen, changed=True)

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
        place = await self._product_place(pickup_point)
        store_id = place.store_id
        filters = [f"isInAssortmentIn:{store_id}", "nonsearchable:false"]
        if in_stock:
            filters.append(f"inStockStore:{store_id}")
        if on_offer:
            filters.append(f"isInOffer:{store_id}")
        if in_leaflet:
            filters.append("isInCurrentLeaflet:true")
        products = await self._query_products(
            place,
            query=text,
            hitsPerPage=limit,
            removeWordsIfNoResults="allOptional",
            filters=" AND ".join(filters),
        )
        return sort_products(products, sort)

    async def get_products(
        self, article_numbers: Iterable[str], *, pickup_point: str | None = None
    ) -> list[Product]:
        """The Products with these Article Numbers at the Store of a Pickup Point, by default
        the Store that serves the Cart, in the order asked.

        Article Numbers Bilka does not know, or that the Store does not sell, are left out.
        Products kept out of `search()` are found too.
        """
        wanted = list(dict.fromkeys(article_numbers))
        for article_number in wanted:
            # Article Numbers go into Algolia's filter as they are, so only digits pass.
            if not (article_number.isascii() and article_number.isdigit()):
                raise ValueError(f"{article_number!r} is not an Article Number")
        if len(wanted) > PRODUCT_LOOKUP_LIMIT:
            raise ValueError(
                f"At most {PRODUCT_LOOKUP_LIMIT} Article Numbers can be looked up at once"
            )
        if not wanted:
            return []
        place = await self._product_place(pickup_point)
        ids = " OR ".join(f"objectID:{article_number}" for article_number in wanted)
        products = await self._query_products(
            place,
            query="",
            hitsPerPage=len(wanted),
            filters=f"({ids}) AND isInAssortmentIn:{place.store_id}",
        )
        by_number = {product.article_number: product for product in products}
        return [by_number[number] for number in wanted if number in by_number]

    async def get_recipe(self, recipe: str) -> RecipeDetails:
        """A Recipe from bilkatogo.dk with its Recipe Products at the Store that serves the
        Cart, in page order; Products the Store does not sell are left out. To add them, pass
        `{product.article_number: 1 for product in recipe.products}` to `add_items_to_cart`."""
        # The recipes module stays apart: a change to the website breaks only recipes (ADR 0007).
        from .recipes import RecipeReader

        async with RecipeReader(httpx_client=self._http) as reader:
            details = await reader.recipe(recipe)
        details.products = await self.get_products(details.article_numbers)
        return details

    async def _product_place(self, pickup_point: str | None) -> _ProductPlace:
        """The Store and `blockbit` of the named Pickup Point, or else of the Cart: its Store
        and, like the website, its own top-level `blockbit`."""
        if pickup_point is not None:
            address = await self._pickup_point_address(pickup_point)
            store_id, place_blockbit = cart_store_id(address), blockbit(address)
        else:
            cart = await self.get_cart_json()
            store_id, place_blockbit = cart_store_id(cart.get("deliveryAddress")), blockbit(cart)
        if store_id is None:
            raise NoPickupPointError(
                "No Pickup Point or Home Delivery address chosen in the Cart; "
                "name a Pickup Point to search at"
            )
        return _ProductPlace(store_id, place_blockbit)

    async def _query_products(self, place: _ProductPlace, **params: Any) -> list[Product]:
        store_id = place.store_id
        params["attributesToRetrieve"] = json.dumps(product_attributes(store_id))
        response = await self._request(
            "POST",
            ALGOLIA_QUERIES_URL,
            headers={
                "x-algolia-application-id": ALGOLIA_APP_ID,
                "x-algolia-api-key": ALGOLIA_SEARCH_KEY,
            },
            json={"requests": [{"indexName": ALGOLIA_PRODUCT_INDEX, "params": urlencode(params)}]},
        )
        if response.status_code != 200:
            raise UnexpectedResponseError(f"Product search failed with HTTP {response.status_code}")
        try:
            [result] = self._json(response, "Product search")["results"]
            hits = result["hits"]
        except (KeyError, TypeError, ValueError):
            raise UnexpectedResponseError("Product search answered without results") from None
        products = (Product.from_hit(hit, store_id, place.blockbit) for hit in hits)
        return [product for product in products if product is not None]

    async def _shop(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        jwt = (
            self._valid_jwt()
            or await self._restore_session()
            or await self._renew_jwt()
            or await self._login()
        )
        data = await self._shop_request(method, path, params, jwt)
        if data is None or _is_session_expired(data):
            # Bilka refused a JWT it should accept; only a login with the password is trusted.
            data = await self._shop_request(method, path, params, await self._login())
        if data is None or _is_session_expired(data):
            reason = f": {data['msg']}" if data is not None else ""
            raise UnexpectedResponseError(f"{path} rejected a fresh session{reason}")
        if _is_error_body(data):
            raise _ShopRefusedError(path, data["msg"], data.get("eid"))
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
        session = await self._saved_session()
        if session is None:
            return None
        self._home_address = self._home_address or _saved_home_address(session)
        try:
            expires_at = datetime.fromisoformat(session["jwt_expires_at"])
            jwt = session["jwt"]
        except (KeyError, TypeError, ValueError):
            return None
        self._jwt = jwt
        self._jwt_expires_at = expires_at
        # A session saved before version 2 has no login token.
        login_token = session.get("login_token")
        self._login_token = login_token if isinstance(login_token, str) else None
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
        if error_code == GIGYA_RATE_LIMIT or (
            isinstance(error_code, int) and error_code >= GIGYA_SERVER_ERRORS
        ):
            message = data.get("errorMessage")
            raise BilkaBusyError(
                f"Gigya {method} answered {error_code}" + (f": {message}" if message else "")
            )
        if error_code != 0:
            raise _GigyaRefusedError(
                f"Gigya {method} failed: {data.get('errorMessage', error_code)}"
            )
        if response.status_code != 200:
            raise UnexpectedResponseError(f"Gigya {method} failed with HTTP {response.status_code}")
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
