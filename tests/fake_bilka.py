import asyncio
import copy
import json
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, parse_qsl

import httpx

from bilkatogo.models.cart import FEE_GROUPS, is_home_delivery

FIXTURES = Path(__file__).parent / "fixtures"

EMAIL = "kunde@example.com"
PASSWORD = "hemmeligt-kodeord"
SESSION_COOKIES = {"PHPSESSID": "fake-phpsessid", "jwt_validation": "fake-jwt-validation"}
ALGOLIA_APP_ID = "F9VBJLR1BK"
ALGOLIA_SEARCH_KEY = "1deaf41c87e729779f7695c00f190cc9"
ALGOLIA_INDEX = "prod_BILKATOGO_PRODUCTS"
UID = 900001
DANISH_WEEKDAYS = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"]
UNAVAILABLE_GROUP = "Ikke tilgængeligt"
# What Bilka charged where the Cart was moved on 2026-11-09, by `facility`: the Delivery Fee
# and Packaging, in øre. A Bilka store charges neither.
FEES_BY_FACILITY = {3: (0, 0), 5: (1900, 500), 8: (3900, 0)}
DELIVERY_FEE_LINE = {
    "quantity": 1,
    "amount": 0,
    "unitprice": 0,
    "unavailable_reason": "",
    "product": {"objectID": 119085, "name": "Leveringsgebyr", "price": 0},
}

# Without a valid JWT Bilka answers shop calls with HTTP 200 and `uid: -1` (captured 2026-11-02).
LOGGED_OUT = {"code": "ACCESS_DENIED", "msg": "Metoden kræver login", "eid": 401, "uid": -1}
ORDER_NOT_FOUND_LOGGED_OUT = {
    "code": None,
    "msg": "Ordren kunne ikke findes.",
    "eid": 404,
    "uid": -1,
}

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "https://accounts.eu1.gigya.com/accounts.login"): "gigya_login",
    ("POST", "https://accounts.eu1.gigya.com/accounts.getJWT"): "gigya_jwt",
    ("POST", "https://api.bilkatogo.dk/api/auth/LoginJWT"): "login_jwt",
    ("POST", "https://api.bilkatogo.dk/api/shop/v7/OrderHistory"): "order_history",
    ("GET", "https://api.bilkatogo.dk/api/shop/v7/OrderDetails"): "order_details",
    (
        "GET",
        "https://api.bilkatogo.dk/api/shop/v6/AvailableDeliveryAddresses",
    ): "available_delivery_addresses",
    ("GET", "https://api.bilkatogo.dk/api/shop/v6/Cart"): "cart",
    (
        "GET",
        "https://api.bilkatogo.dk/api/shop/v3/AvailableDeliveryDates",
    ): "available_delivery_dates",
    ("GET", "https://api.bilkatogo.dk/api/shop/v3/SetDeliveryDate"): "set_delivery_date",
    ("GET", "https://api.bilkatogo.dk/api/shop/v6/SetDeliveryAddress"): "set_delivery_address",
    ("GET", "https://api.bilkatogo.dk/api/shop/v6/ChangeLineCount"): "change_line_count",
    ("GET", "https://api.bilkatogo.dk/api/shop/v6/EmptyCart"): "empty_cart",
    ("POST", "https://f9vbjlr1bk-dsn.algolia.net/1/indexes/*/queries"): "algolia",
}

WEBSITE_HOST = "www.bilkatogo.dk"
RECIPE_FIXTURES = FIXTURES / "recipes"
# The website's build when the recipe pages were captured.
RECIPES_BUILD = "1793920097"
RECIPE_PAGES = {
    "": "opskrifter",
    "madplaner": "madplaner",
    "aftensmad": "aftensmad",
    "aftensmad/danske-retter": "danske-retter",
    "aftensmad/danske-retter/karbonader": "karbonader",
    "aftensmad/pastaretter/carbonara": "carbonara",
    "aftensmad/sandwiches-pita-og-wraps": "sandwiches-pita-og-wraps",
}

type Handler = Callable[[httpx.Request], httpx.Response]
type Scripted = httpx.Response | dict[str, Any] | Exception | Handler


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def sent_cookies(request: httpx.Request) -> dict[str, str]:
    pairs = (part.split("=", 1) for part in request.headers.get("cookie", "").split("; "))
    return {pair[0]: pair[1] for pair in pairs if len(pair) == 2}


def algolia_query(request: httpx.Request) -> dict[str, str]:
    [query] = json.loads(request.content)["requests"]
    return {"indexName": query["indexName"], **dict(parse_qsl(query["params"]))}


class FakeBilka:
    """Fake Bilka backend: Gigya login, the shop API and the Algolia product index, served
    through httpx.MockTransport.

    Each request is matched against ROUTES to a route name. If a script was queued for that
    route with `script()`, the next scripted response is used; otherwise the default handler
    method of the same name answers, which behaves like the real backend for one account.

    Like Bilka, LoginJWT hands out a `cart` cookie and, when a login presents one it handed
    out before, replaces the Cart with the Cart as it was at that earlier login. The website
    calls it; the client must not.
    """

    def __init__(self, email: str = EMAIL, password: str = PASSWORD) -> None:
        self.email = email
        self.password = password
        self.requests: list[httpx.Request] = []
        self.orders: list[dict[str, Any]] = load_fixture("order_history.json")["orders"]
        self.details: dict[str, dict[str, Any]] = {
            details["order"]["orderId"]: details
            for details in map(
                load_fixture,
                [
                    "order_details_substituted.json",
                    "order_details_plain.json",
                    "order_details_missing.json",
                    "order_details_captured.json",
                    "order_details_substituted_captured.json",
                ],
            )
        }
        self.delivery_addresses: dict[str, Any] = load_fixture("available_delivery_addresses.json")
        # The account's Home Address, which SetDeliveryAddress accepts even while it is not
        # listed, and the Article Numbers each Store does not sell.
        self.home_address: dict[str, Any] = load_fixture("cart_home.json")["deliveryAddress"]
        self.unsold: dict[str, set[str]] = {}
        # Where the Cart's chosen Pickup Slot is offered: the place it starts at, by default.
        self.slot_offered_at: set[str] | None = None
        self._headlines: dict[str, str] = {}
        self.cart_contents: dict[str, Any] = load_fixture("cart.json")
        self.stored_carts: dict[str, dict[str, Any]] = {}
        self.products: list[dict[str, Any]] = load_fixture("algolia_products.json")["hits"]
        self.change_answer: dict[str, Any] = load_fixture("change_line_count.json")
        self.max_quantity: int | None = None
        self.build = RECIPES_BUILD
        self.recipe_pages: dict[str, str] = dict(RECIPE_PAGES)
        self.missing_recipe_pages: set[str] = set()
        self.pages_in_flight = 0
        self.most_pages_in_flight = 0
        self._scripts: dict[str, deque[Scripted]] = defaultdict(deque)
        # The website's handler is a coroutine, which MockTransport awaits when used by an
        # AsyncClient; every other route answers at once, also for sync callers (tests/ha).
        self.transport = httpx.MockTransport(cast("Handler", self._handle))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport)

    def script(self, route: str, *responses: Scripted) -> None:
        if route not in {*ROUTES.values(), "website"}:
            raise ValueError(f"Unknown route {route!r}")
        self._scripts[route].extend(responses)

    def calls(self, route: str) -> list[httpx.Request]:
        return [r for r in self.requests if self._route_of(r) == route]

    def _route_of(self, request: httpx.Request) -> str | None:
        if request.method == "GET" and request.url.host == WEBSITE_HOST:
            return "website"
        url = request.url.copy_with(query=None)
        return ROUTES.get((request.method, str(url)))

    def _handle(self, request: httpx.Request) -> httpx.Response | Awaitable[httpx.Response]:
        self.requests.append(request)
        route = self._route_of(request)
        if route is None:
            return httpx.Response(404, json={"error": f"fake: no route for {request.url}"})
        if self._scripts[route]:
            return self._play(self._scripts[route].popleft(), request)
        return getattr(self, route)(request)

    @staticmethod
    def _play(scripted: Scripted, request: httpx.Request) -> httpx.Response:
        if isinstance(scripted, Exception):
            raise scripted
        if isinstance(scripted, httpx.Response):
            return scripted
        if isinstance(scripted, dict):
            return httpx.Response(200, json=scripted)
        return scripted(request)

    def has_shop_session(self, request: httpx.Request) -> bool:
        """Like Bilka, the shop accepts Gigya's JWT on its own, without LoginJWT or cookies."""
        return (
            request.headers.get("jwt_token") == load_fixture("gigya_jwt.json")["id_token"]
            and request.url.params.get("u") == "w"
        )

    def gigya_login(self, request: httpx.Request) -> httpx.Response:
        data = form(request)
        if data.get("loginID") != self.email or data.get("password") != self.password:
            return httpx.Response(403, json=load_fixture("gigya_login_invalid.json"))
        return httpx.Response(200, json=load_fixture("gigya_login.json"))

    def gigya_jwt(self, request: httpx.Request) -> httpx.Response:
        login_token = load_fixture("gigya_login.json")["sessionInfo"]["cookieValue"]
        if form(request).get("login_token") != login_token:
            return httpx.Response(
                403, json={"statusCode": 403, "errorCode": 403005, "errorMessage": "Unauthorized"}
            )
        return httpx.Response(200, json=load_fixture("gigya_jwt.json"))

    def login_jwt(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("jwt_token") != load_fixture("gigya_jwt.json")["id_token"]:
            return httpx.Response(401, json={"code": 401, "msg": "Unauthorized"})
        if (cart_id := sent_cookies(request).get("cart")) in self.stored_carts:
            self.cart_contents = copy.deepcopy(self.stored_carts[cart_id])
        cart_id = f"fake-cart-{len(self.stored_carts) + 1}"
        self.stored_carts[cart_id] = copy.deepcopy(self.cart_contents)
        cookies = {**SESSION_COOKIES, "cart": cart_id}
        headers = [("set-cookie", f"{k}={v}; path=/") for k, v in cookies.items()]
        return httpx.Response(200, json=load_fixture("login_jwt.json"), headers=headers)

    def order_history(self, request: httpx.Request) -> httpx.Response:
        if not self.has_shop_session(request):
            return httpx.Response(200, json=LOGGED_OUT)
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        page = self.orders[offset : offset + limit]
        return httpx.Response(200, json={"total": len(self.orders), "orders": page})

    def order_details(self, request: httpx.Request) -> httpx.Response:
        if not self.has_shop_session(request):
            return httpx.Response(200, json=ORDER_NOT_FOUND_LOGGED_OUT)
        details = self.details.get(request.url.params.get("orderId", ""))
        if details is None:
            return httpx.Response(404, json={"code": 404, "msg": "Not found"})
        return httpx.Response(200, json=details)

    def available_delivery_addresses(self, request: httpx.Request) -> httpx.Response:
        """The places the Cart can go. Like Bilka, the account's Home Address is listed only
        while the Cart is on Home Delivery, and `activeAddress` is the Cart's place (captured
        2026-11-09)."""
        if not self.has_shop_session(request):
            return httpx.Response(200, json={**self.delivery_addresses, "uid": -1})
        listing = copy.deepcopy(self.delivery_addresses)
        if active := self.cart_contents.get("deliveryAddress"):
            listing["activeAddress"] = copy.deepcopy(active)
            if is_home_delivery(active):
                listing["addresses"].append(copy.deepcopy(active))
        return httpx.Response(200, json={**listing, "uid": UID})

    def cart(self, request: httpx.Request) -> httpx.Response:
        if not self.has_shop_session(request):
            return httpx.Response(200, json=load_fixture("cart.json"))
        return httpx.Response(200, json={**self.cart_contents, "uid": UID})

    def available_delivery_dates(self, request: httpx.Request) -> httpx.Response:
        """The Pickup Slots for whatever the Cart has: its Pickup Point or Home Delivery."""
        # Bilka's logged-out answer was not captured; a guest answer with `uid: -1` like Cart's.
        uid = UID if self.has_shop_session(request) else -1
        return httpx.Response(200, json={**self._slot_listing(), "uid": uid})

    def _slot_listing(self) -> dict[str, Any]:
        home = is_home_delivery(self.cart_contents.get("deliveryAddress"))
        return load_fixture(f"available_delivery_dates_{'home' if home else 'pickup'}.json")

    def set_delivery_date(self, request: httpx.Request) -> httpx.Response:
        """Chooses the Cart's Pickup Slot. Like Bilka, a slot it does not list as open is
        refused with `eid` 406 and the Cart keeps its slot (captured 2026-11-09)."""
        # Bilka's answer to a logged-out SetDeliveryDate was not captured.
        if not self.has_shop_session(request):
            return httpx.Response(401, json={"code": 401, "msg": "Unauthorized"})
        params = request.url.params
        day, start, end = params["deliveryDate"], params["intervalStart"], params["intervalEnd"]
        if not self._is_open(day, start, end):
            refused = {**load_fixture("set_delivery_date_unavailable.json"), "deliveryDate": day}
            return httpx.Response(200, json=refused)
        weekday = DANISH_WEEKDAYS[date.fromisoformat(day).weekday()]
        shown = f"{weekday}. {day[8:10]}/{day[5:7]}."
        chosen = {
            "displayDeliveryDate": day,
            "deliveryDate": day,
            "deliveryDay": weekday,
            "intervalStart": start,
            "intervalEnd": end,
            "closed": 0,
            "delivery_message": f"{shown} Senest kl. {end}"
            if start == "00:00"
            else f"{shown} kl. {start} - {end}",
        }
        self.cart_contents["deliveryDate"] = {
            **(self.cart_contents.get("deliveryDate") or {}),
            **chosen,
        }
        answer = {
            **load_fixture("set_delivery_date.json"),
            **chosen,
            "stat": self.cart_contents["stat"],
            "lines": self.cart_contents["lines"],
        }
        return httpx.Response(200, json=answer)

    def set_delivery_address(self, request: httpx.Request) -> httpx.Response:
        """Moves the Cart to a Pickup Point or the Home Address by `id`, as captured on
        2026-11-09: the Store, fees and Minimum Order follow the place, Cart Lines the Store
        does not sell are kept under "Ikke tilgængeligt", and the chosen Pickup Slot is kept,
        closed where it is not offered. An `id` Bilka does not know is refused with `eid` 406
        and the Cart stays."""
        # Bilka's answer to a logged-out SetDeliveryAddress was not captured.
        if not self.has_shop_session(request):
            return httpx.Response(401, json={"code": 401, "msg": "Unauthorized"})
        params = request.url.params
        place = self._place(params["id"], params.get("zipcode", ""))
        if place is None:
            return httpx.Response(200, json=load_fixture("set_delivery_address_unknown.json"))
        self._move_cart(place)
        answer = {
            **load_fixture("set_delivery_address.json"),
            "activeAddress": copy.deepcopy(place),
        }
        return httpx.Response(200, json=answer)

    def move_cart_to(self, name: str) -> None:
        """Puts the Cart at the listed place with this name, as SetDeliveryAddress would."""
        [address] = [a for a in self.delivery_addresses["addresses"] if a["name"] == name]
        self._move_cart(copy.deepcopy(address))

    def _move_cart(self, place: dict[str, Any]) -> None:
        cart = self.cart_contents
        if self.slot_offered_at is None:
            self.slot_offered_at = {str((cart.get("deliveryAddress") or {}).get("id"))}
        cart["deliveryAddress"] = place
        # Bilka copies the place's `blockbit` to the top of the Cart (captured 2026-11-09).
        cart["blockbit"] = place["blockbit"]
        if cart.get("deliveryDate"):
            cart["deliveryDate"]["closed"] = 0 if place["id"] in self.slot_offered_at else 1
        self._charge_fees(place["facility"])
        self._mark_unsold(self.unsold.get(place["sid"], set()))

    def _place(self, place_id: str, zip_code: str) -> dict[str, Any] | None:
        if place_id == self.home_address["id"]:
            # Bilka keeps the zip code sent as the Home Address's `temporary_zipcode`.
            return {**copy.deepcopy(self.home_address), "temporary_zipcode": zip_code}
        for address in self.delivery_addresses["addresses"]:
            if address["id"] == place_id:
                return copy.deepcopy(address)
        return None

    def _charge_fees(self, facility: int) -> None:
        stat = self.cart_contents["stat"]
        delivery_fee, packaging = FEES_BY_FACILITY[facility]
        stat["price"] += (
            delivery_fee
            - (stat.get("delivery_price") or 0)
            + packaging
            - (stat.get("packaging_price") or 0)
        )
        stat.update(delivery_price=delivery_fee, packaging_price=packaging)
        groups: list[dict[str, Any]] = self.cart_contents["lines"]
        services: dict[str, Any] | None = next(
            (group for group in groups if group["headline"] == "Services"), None
        )
        if services is None:
            services = {"headline": "Services", "type": "", "lines": []}
            groups.insert(0, services)
        blocks: list[dict[str, Any]] = [
            block
            for block in services["lines"]
            if not any(line["product"]["name"] == "Leveringsgebyr" for line in block["orderlines"])
        ]
        if delivery_fee:
            line = copy.deepcopy(DELIVERY_FEE_LINE)
            line.update(amount=delivery_fee, unitprice=delivery_fee)
            line["product"]["price"] = delivery_fee
            blocks.append({"orderlines": [line]})
        services["lines"] = blocks

    def _mark_unsold(self, unsold: set[str]) -> None:
        """Puts the Cart Lines in `unsold` under "Ikke tilgængeligt" with Bilka's reason, and
        the others back under their own headline."""
        fees, lines = [], []
        for group in self.cart_contents["lines"]:
            if group["headline"] in FEE_GROUPS:
                fees.append(group)
                continue
            for block in group["lines"]:
                for line in block["orderlines"]:
                    article_number = str(line["product"]["objectID"])
                    if group["headline"] != UNAVAILABLE_GROUP:
                        self._headlines[article_number] = group["headline"]
                    lines.append(line)
        groups: dict[str, dict[str, Any]] = {}
        for line in lines:
            article_number = str(line["product"]["objectID"])
            line["unavailable_reason"] = "Udsolgt" if article_number in unsold else ""
            headline = (
                UNAVAILABLE_GROUP if article_number in unsold else self._headlines[article_number]
            )
            group = groups.setdefault(headline, {"headline": headline, "type": "", "lines": []})
            group["lines"].append({"orderlines": [line]})
        unavailable = groups.pop(UNAVAILABLE_GROUP, None)
        self.cart_contents["lines"] = [
            *fees,
            *groups.values(),
            *([unavailable] if unavailable else []),
        ]

    def _is_open(self, day: str, start: str, end: str) -> bool:
        for day_data in self._slot_listing()["availableIntervals"]:
            if day_data["date"] != day:
                continue
            for interval in day_data.get("intervals") or []:
                if (interval["intervalStart"], interval["intervalEnd"]) == (start, end):
                    return not interval.get("closed") and interval["availability_bit"] & 1
        return False

    def change_line_count(self, request: httpx.Request) -> httpx.Response:
        # Bilka's answer to a logged-out change was not captured.
        if not self.has_shop_session(request):
            return httpx.Response(401, json={"code": 401, "msg": "Unauthorized"})
        article_number = request.url.params["productId"]
        count = int(request.url.params["count"])
        if self.max_quantity is not None:
            count = min(count, self.max_quantity)
        self._set_line(article_number, count)
        answer = dict(self.change_answer)
        answer.update(productId=article_number, productAmount=count)
        return httpx.Response(200, json=answer)

    def _set_line(self, article_number: str, count: int) -> None:
        for group in self.cart_contents["lines"]:
            for block in group["lines"]:
                for line in block["orderlines"]:
                    if str(line["product"]["objectID"]) == article_number:
                        line.update(quantity=count, amount=count * line["unitprice"])
                        if count == 0:
                            block["orderlines"].remove(line)
                        return
        if count == 0:
            return
        [hit] = [hit for hit in self.products if hit["objectID"] == article_number]
        line = {
            "product": {
                "objectID": int(article_number),
                "name": hit["name"],
                "price": hit["price"],
            },
            "quantity": count,
            "unitprice": hit["price"],
            "amount": count * hit["price"],
        }
        self.cart_contents["lines"].append(
            {"headline": "Andet", "type": "products", "lines": [{"orderlines": [line]}]}
        )

    def empty_cart(self, request: httpx.Request) -> httpx.Response:
        # Bilka's answer to a logged-out EmptyCart was not captured.
        if not self.has_shop_session(request):
            return httpx.Response(401, json={"code": 401, "msg": "Unauthorized"})
        self.cart_contents["lines"] = [
            group for group in self.cart_contents["lines"] if group["headline"] in FEE_GROUPS
        ]
        return httpx.Response(200, json={**self.cart_contents, "uid": UID})

    async def website(self, request: httpx.Request) -> httpx.Response:
        """bilkatogo.dk: the recipes page's HTML with the build id, and each page's
        payload.js under that build. Anything else answers like the real site, with HTTP 200
        and the website's HTML naming the current build: a page it does not know and a
        payload under an old build alike (captured 2026-11-08)."""
        self.pages_in_flight += 1
        self.most_pages_in_flight = max(self.most_pages_in_flight, self.pages_in_flight)
        try:
            for _ in range(3):  # let other reads start, to count how many run at once
                await asyncio.sleep(0)
            path = request.url.path
            if path == "/inspiration/opskrifter/":
                return httpx.Response(200, text=self._website_html("opskrifter.html"))
            prefix = f"/_nuxt/static/{self.build}/inspiration/opskrifter"
            if path.startswith(prefix) and path.endswith("/payload.js"):
                page = path.removeprefix(prefix).removesuffix("/payload.js").strip("/")
                if name := self._recipe_page(page):
                    payload = (RECIPE_FIXTURES / f"{name}.payload.js").read_text(encoding="utf-8")
                    return httpx.Response(200, text=payload)
            return httpx.Response(200, text=self._website_html("unknown-page.html"))
        finally:
            self.pages_in_flight -= 1

    def _recipe_page(self, page: str) -> str | None:
        """The fixture for a recipe page. The categories below aftensmad that were not
        captured answer with a captured one: a sub-category with Danish dishes, a recipe with
        karbonader."""
        if page in self.missing_recipe_pages:
            return None
        if page in self.recipe_pages:
            return self.recipe_pages[page]
        if page.startswith("aftensmad/"):
            return "danske-retter" if page.count("/") == 1 else "karbonader"
        return None

    def _website_html(self, name: str) -> str:
        html = (RECIPE_FIXTURES / name).read_text(encoding="utf-8")
        return html.replace(RECIPES_BUILD, self.build)

    def algolia(self, request: httpx.Request) -> httpx.Response:
        if (
            request.headers.get("x-algolia-application-id") != ALGOLIA_APP_ID
            or request.headers.get("x-algolia-api-key") != ALGOLIA_SEARCH_KEY
        ):
            return httpx.Response(403, json={"message": "Invalid Application-ID or API key"})
        query = algolia_query(request)
        if query["indexName"] != ALGOLIA_INDEX:
            return httpx.Response(404, json={"message": "Index does not exist"})
        filters: dict[str, str] = {}
        object_ids: set[str] | None = None
        for term in query.get("filters", "").split(" AND "):
            if term.startswith("(") and term.endswith(")"):
                alternatives = [part.split(":", 1) for part in term[1:-1].split(" OR ")]
                if any(name != "objectID" for name, _ in alternatives):
                    return httpx.Response(400, json={"message": f"fake: unexpected group {term}"})
                object_ids = {value for _, value in alternatives}
            elif term:
                name, value = term.split(":", 1)
                filters[name] = value
        if "isInAssortmentIn" not in filters or filters.get("nonsearchable", "false") != "false":
            return httpx.Response(400, json={"message": "fake: unexpected filters"})
        store = int(filters.pop("isInAssortmentIn"))
        searchable_only = filters.pop("nonsearchable", None) is not None
        known = {"inStockStore": str(store), "isInOffer": str(store), "isInCurrentLeaflet": "true"}
        if any(known.get(name) != value for name, value in filters.items()):
            return httpx.Response(400, json={"message": f"fake: unexpected filters {filters}"})
        per_store = {"inStockStore", "isInOffer"}
        words = query.get("query", "").casefold().split()
        hits = [
            hit
            for hit in self.products
            if store in hit["isInAssortmentIn"]
            and (object_ids is None or str(hit["objectID"]) in object_ids)
            and not (searchable_only and hit.get("nonsearchable"))
            and all(store in hit[name] for name in per_store & filters.keys())
            and (hit["isInCurrentLeaflet"] or "isInCurrentLeaflet" not in filters)
            and all(word in f"{hit['name']} {hit['brand']}".casefold() for word in words)
        ][: int(query.get("hitsPerPage", 20))]
        attributes = json.loads(query.get("attributesToRetrieve", "[]"))
        hits = [retrieve(hit, attributes) for hit in hits]
        return httpx.Response(
            200, json={"results": [{"hits": hits, "nbHits": len(hits), "index": ALGOLIA_INDEX}]}
        )


def retrieve(hit: dict[str, Any], attributes: list[str]) -> dict[str, Any]:
    """The real index returns only objectID unless attributesToRetrieve names more."""
    if "*" in attributes:
        return hit
    picked: dict[str, Any] = {"objectID": hit["objectID"]}
    for attribute in attributes:
        name, _, key = attribute.partition(".")
        if name not in hit:
            continue
        if key:
            if key in hit[name]:
                picked.setdefault(name, {})[key] = hit[name][key]
        else:
            picked[name] = hit[name]
    return picked
