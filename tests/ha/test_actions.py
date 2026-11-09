from typing import Any

import httpx
import probatio
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, algolia_query, load_fixture
from tests.ha.conftest import SetupIntegration

ACTIONS = [
    ("add_to_cart", {"article_number": "19753"}),
    ("add_items_to_cart", {"items": [{"article_number": "19753"}]}),
    ("remove_from_cart", {"article_number": "18381"}),
    ("search", {"query": "skyr"}),
    ("get_cart", {}),
]


@pytest.fixture
def cart(bilka: FakeBilka) -> FakeBilka:
    bilka.cart_contents = load_fixture("cart_logged_in.json")
    return bilka


async def call(
    hass: HomeAssistant, action: str, data: dict[str, Any], *, return_response: bool = True
) -> Any:
    return await hass.services.async_call(
        DOMAIN, action, data, blocking=True, return_response=return_response
    )


async def test_add_to_cart_adds_to_an_existing_cart_line_and_returns_the_cart(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()

    response = await call(hass, "add_to_cart", {"article_number": "18381", "quantity": 2})

    [request] = cart.calls("change_line_count")
    assert request.url.params["count"] == "12"
    assert response["lines"][0] == {
        "product": "Bananer",
        "article_number": "18381",
        "quantity": 12,
        "price": 2.75,
        "line_total": 30.0,
    }
    assert len(response["lines"]) == 5
    assert {key: value for key, value in response.items() if key != "lines"} == {
        "total": 127.0,
        "deposit": 0.0,
        "service_fee": 39.0,
        "packaging": 0.0,
        "discount": 9.5,
        "delivery_fee": 0.0,
        "home_delivery": False,
        "pickup_point": "Bilka Vejle",
        "pickup_slot": None,
        "minimum_order": None,
        "minimum_left": 0.0,
        "next_available_date": "2026-10-27",
        "warnings": [],
    }


@pytest.fixture
def no_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("bilkatogo.client.sleep", sleep)


@pytest.mark.usefixtures("no_pauses")
async def test_add_items_to_cart_adds_each_and_returns_the_results_and_cart(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()

    response = await call(
        hass,
        "add_items_to_cart",
        {
            "items": [
                {"article_number": "18381", "quantity": 2},
                {"article_number": "19753"},
                {"article_number": "18381", "quantity": 1},
            ]
        },
    )

    counts = [request.url.params["count"] for request in cart.calls("change_line_count")]
    assert counts == ["13", "1"]
    assert response["changes"] == [
        {
            "article_number": "18381",
            "requested": 13,
            "applied": 13,
            "status": "applied",
            "message": None,
        },
        {
            "article_number": "19753",
            "requested": 1,
            "applied": 1,
            "status": "applied",
            "message": None,
        },
    ]
    assert response["cart"]["lines"][0]["quantity"] == 13
    assert response["cart"]["pickup_point"] == "Bilka Vejle"


@pytest.mark.usefixtures("no_pauses")
async def test_add_items_to_cart_reports_a_rejected_item_and_adds_the_rest(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()
    cart.script("change_line_count", load_fixture("change_line_count_unavailable.json"))

    response = await call(
        hass,
        "add_items_to_cart",
        {"items": [{"article_number": "88672"}, {"article_number": "19753"}]},
    )

    rejected, added = response["changes"]
    assert rejected["status"] == "rejected"
    assert "ikke aktuelt tilgængeligt" in rejected["message"]
    assert added["status"] == "applied"


@pytest.mark.usefixtures("no_pauses")
async def test_add_items_to_cart_fails_when_none_was_added(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()
    cart.script("change_line_count", load_fixture("change_line_count_unavailable.json"))

    with pytest.raises(HomeAssistantError) as failed:
        await call(hass, "add_items_to_cart", {"items": [{"article_number": "88672"}]})

    assert failed.value.translation_key == "no_items_added"
    placeholders = failed.value.translation_placeholders
    assert placeholders is not None
    assert "88672: Produktet" in placeholders["error"]


@pytest.mark.parametrize(
    "items",
    [[], [{"quantity": 2}], [{"article_number": "19753", "quantity": 0}]],
)
async def test_add_items_to_cart_refuses_malformed_items(
    hass: HomeAssistant, setup_integration: SetupIntegration, items: list[dict[str, Any]]
) -> None:
    await setup_integration()

    with pytest.raises(probatio.Invalid):
        await call(hass, "add_items_to_cart", {"items": items})


async def test_add_to_cart_creates_a_cart_line_and_returns_nothing_unless_asked(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()

    response = await call(hass, "add_to_cart", {"article_number": "19753"}, return_response=False)

    [request] = cart.calls("change_line_count")
    assert request.url.params["count"] == "1"
    assert response is None


async def test_a_rejected_change_carries_bilkas_message(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()
    cart.max_quantity = 2
    cart.change_answer = {
        **load_fixture("change_line_count.json"),
        "offerLimitMessage": "Maks. 2 pr. kunde",
    }

    with pytest.raises(HomeAssistantError) as rejected:
        await call(hass, "add_to_cart", {"article_number": "18364", "quantity": 4})

    assert rejected.value.translation_key == "cart_change_rejected"
    assert rejected.value.translation_placeholders == {"error": "Maks. 2 pr. kunde"}


@pytest.mark.parametrize("action", ["add_to_cart", "remove_from_cart"])
async def test_quantity_must_be_at_least_one(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka, action: str
) -> None:
    await setup_integration()

    with pytest.raises(probatio.Invalid):
        await call(hass, action, {"article_number": "18381", "quantity": 0})

    assert cart.calls("change_line_count") == []


async def test_remove_from_cart_takes_a_quantity_off_and_returns_the_cart(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()

    response = await call(hass, "remove_from_cart", {"article_number": "18381", "quantity": 3})

    [request] = cart.calls("change_line_count")
    assert request.url.params["count"] == "7"
    assert response["lines"][0]["quantity"] == 7


async def test_remove_from_cart_without_a_quantity_drops_the_cart_line(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()

    response = await call(
        hass, "remove_from_cart", {"article_number": "18364"}, return_response=False
    )

    [request] = cart.calls("change_line_count")
    assert request.url.params["count"] == "0"
    assert response is None


async def test_get_cart_returns_the_cart_without_changing_it(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-30T12:00:00+01:00")
    await setup_integration()

    response = await call(hass, "get_cart", {})

    assert cart.calls("change_line_count") == []
    assert [(line["article_number"], line["quantity"]) for line in response["lines"]] == [
        ("18381", 10),
        ("18364", 1),
        ("19789", 1),
        ("185370", 1),
        ("75191", 1),
    ]
    assert response["lines"][0] == {
        "product": "Bananer",
        "article_number": "18381",
        "quantity": 10,
        "price": 2.75,
        "line_total": 25.0,
    }
    assert {key: value for key, value in response.items() if key != "lines"} == {
        "total": 127.0,
        "deposit": 0.0,
        "service_fee": 39.0,
        "packaging": 0.0,
        "discount": 9.5,
        "delivery_fee": 0.0,
        "home_delivery": False,
        "pickup_point": "Bilka Vejle",
        "pickup_slot": {
            "starts_at": "2026-11-01T14:00:00+01:00",
            "ends_at": "2026-11-01T16:00:00+01:00",
        },
        "minimum_order": None,
        "minimum_left": 0.0,
        "next_available_date": "2026-10-27",
        "warnings": [],
    }


async def test_get_cart_for_home_delivery_leaves_out_the_address(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    home_delivery_cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-11-08T12:00:00+01:00")
    await setup_integration()

    response = await call(hass, "get_cart", {})

    assert {key: value for key, value in response.items() if key != "lines"} == {
        "total": 101.4,
        "deposit": 0.0,
        "service_fee": 39.0,
        "packaging": 0.0,
        "discount": 0.0,
        "delivery_fee": 39.0,
        "home_delivery": True,
        "pickup_point": None,
        "pickup_slot": {
            "starts_at": "2026-11-11T15:00:00+01:00",
            "ends_at": "2026-11-11T22:00:00+01:00",
        },
        "minimum_order": 500.0,
        "minimum_left": 398.6,
        "next_available_date": "2026-11-09",
        "warnings": ["398,60 kr. fra minimumskøb på 500,00 kr. alt inkl. for at få hjemmelevering"],
    }
    for personal in ("Kunde Eksempel", "Testvej", "Eksempelby", "9999"):
        assert personal not in str(response)


async def test_search_returns_products_at_the_carts_store(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    await setup_integration()

    response = await call(hass, "search", {"query": "skyr"})

    [request] = bilka.calls("algolia")
    query = algolia_query(request)
    assert query["hitsPerPage"] == "10"
    assert "isInAssortmentIn:1653" in query["filters"]
    assert response == {
        "products": [
            {
                "article_number": "19753",
                "name": "Skyr m. vanilje 0,2% fedt u. tilsat sukker",
                "brand": "Cheasy",
                "price": 30.95,
                "unit_price": 30.95,
                "unit": "kg",
                "in_stock": True,
                "offer": None,
                "multibuy_offer": None,
            },
            {
                "article_number": "128298",
                "name": "Skyr m. vanilje 0,2 % fedt øko",
                "brand": "Salling ØKO",
                "price": 39.31,
                "unit_price": 49.14,
                "unit": "kg",
                "in_stock": True,
                "offer": {
                    "label": "Skarp pris",
                    "price": 25.0,
                    "unit_price": 31.25,
                    "ends": None,
                    "limit_per_customer": None,
                },
                "multibuy_offer": None,
            },
        ]
    }


async def test_search_returns_the_offer_end_date_and_multibuy_offer(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    await setup_integration()

    chicken = await call(hass, "search", {"query": "kyllingebrystfilet"})
    bananas = await call(hass, "search", {"query": "bananer"})

    assert chicken["products"][0]["offer"] == {
        "label": "Spar 4,95",
        "price": 60.0,
        "unit_price": 92.31,
        "ends": "2026-10-29",
        "limit_per_customer": None,
    }
    assert bananas["products"][0]["offer"] is None
    assert bananas["products"][0]["multibuy_offer"] == "Mix 10 for 25,00"


async def test_search_limit_caps_the_number_of_products(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    await setup_integration()

    response = await call(hass, "search", {"query": "skyr", "limit": 1})

    assert [product["article_number"] for product in response["products"]] == ["19753"]


async def test_search_filters_and_sorts(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    await setup_integration()

    response = await call(
        hass, "search", {"query": "e", "in_stock": True, "on_offer": True, "sort": "price"}
    )

    assert [product["article_number"] for product in response["products"]] == ["18381", "128298"]
    query = algolia_query(bilka.calls("algolia")[-1])
    assert query["filters"] == (
        "isInAssortmentIn:1653 AND nonsearchable:false AND inStockStore:1653 AND isInOffer:1653"
    )


async def test_search_in_leaflet(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    await setup_integration()

    response = await call(hass, "search", {"query": "e", "in_leaflet": True})

    assert response["products"] == []
    assert algolia_query(bilka.calls("algolia")[-1])["filters"].endswith(
        " AND isInCurrentLeaflet:true"
    )


async def test_search_with_an_unknown_sort_is_refused(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    await setup_integration()

    with pytest.raises(probatio.Invalid):
        await call(hass, "search", {"query": "skyr", "sort": "popularity"})


async def test_search_without_a_pickup_point_asks_to_choose_one(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    await setup_integration()
    bilka.cart_contents = {**bilka.cart_contents, "deliveryAddress": None}

    with pytest.raises(HomeAssistantError) as failed:
        await call(hass, "search", {"query": "skyr"})

    assert failed.value.translation_key == "no_pickup_point"
    assert bilka.calls("algolia") == []


@pytest.mark.parametrize(("action", "data"), ACTIONS)
async def test_one_entry_needs_no_config_entry_id(
    hass: HomeAssistant, setup_integration: SetupIntegration, action: str, data: dict[str, Any]
) -> None:
    await setup_integration()

    assert await call(hass, action, data)


@pytest.mark.parametrize(("action", "data"), ACTIONS)
async def test_several_entries_need_a_config_entry_id(
    hass: HomeAssistant, setup_integration: SetupIntegration, action: str, data: dict[str, Any]
) -> None:
    other = MockConfigEntry(
        domain=DOMAIN,
        title="anden@example.com",
        unique_id="anden@example.com",
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    other.add_to_hass(hass)
    entry = await setup_integration()
    assert other.state is ConfigEntryState.LOADED

    with pytest.raises(ServiceValidationError) as missing:
        await call(hass, action, data)

    assert missing.value.translation_key == "entry_required"
    assert await call(hass, action, {**data, "config_entry_id": entry.entry_id})


async def test_an_unknown_config_entry_id_is_rejected(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    await setup_integration()

    with pytest.raises(ServiceValidationError) as unknown:
        await call(hass, "search", {"query": "skyr", "config_entry_id": "nope"})

    assert unknown.value.translation_key == "entry_not_found"


async def test_an_unloaded_entry_is_rejected(
    hass: HomeAssistant, setup_integration: SetupIntegration
) -> None:
    entry = await setup_integration()
    await hass.config_entries.async_unload(entry.entry_id)

    with pytest.raises(ServiceValidationError) as unloaded:
        await call(hass, "search", {"query": "skyr", "config_entry_id": entry.entry_id})
    with pytest.raises(ServiceValidationError) as none_loaded:
        await call(hass, "search", {"query": "skyr"})

    assert unloaded.value.translation_key == "entry_not_loaded"
    assert none_loaded.value.translation_key == "no_entry_loaded"


async def test_a_changed_password_starts_reauth(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    entry = await setup_integration()
    bilka.password = "nyt-kodeord"
    bilka.script("cart", httpx.Response(401, json={"code": 401, "msg": "Unauthorized"}))

    with pytest.raises(HomeAssistantError) as failed:
        await call(hass, "search", {"query": "skyr"})
    await hass.async_block_till_done()

    assert failed.value.translation_key == "auth_failed"
    assert [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("source") == SOURCE_REAUTH
        and flow["context"].get("entry_id") == entry.entry_id
    ]


async def test_bilka_failures_become_a_translated_error(
    hass: HomeAssistant, setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    await setup_integration()
    bilka.script("algolia", httpx.ConnectError("no route to host"))

    with pytest.raises(HomeAssistantError) as failed:
        await call(hass, "search", {"query": "skyr"})

    assert failed.value.translation_key == "action_failed"


PICKUP_CAPTURED = "2026-11-08T20:00:00+01:00"
HOME_CAPTURED = "2026-11-02T08:00:00+01:00"


async def test_choose_pickup_slot_returns_the_chosen_and_replaced_slot_and_refreshes(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    cart.cart_contents["deliveryDate"].update(deliveryDate="2026-11-11")
    await setup_integration()
    polls = len(cart.calls("order_history"))

    response = await call(hass, "choose_pickup_slot", {"date": "2026-11-09", "start": "16:00"})

    [request] = cart.calls("set_delivery_date")
    assert (request.url.params["intervalStart"], request.url.params["intervalEnd"]) == (
        "16:00",
        "18:00",
    )
    assert response == {
        "chosen": {
            "starts_at": "2026-11-09T16:00:00+01:00",
            "ends_at": "2026-11-09T18:00:00+01:00",
        },
        "replaced": {
            "starts_at": "2026-11-11T14:00:00+01:00",
            "ends_at": "2026-11-11T16:00:00+01:00",
        },
        "changed": True,
    }
    assert len(cart.calls("order_history")) == polls + 1


async def test_choose_pickup_slot_with_an_end_and_without_a_response(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    await setup_integration()

    response = await call(
        hass,
        "choose_pickup_slot",
        {"date": "2026-11-10", "start": "18:00", "end": "22:00"},
        return_response=False,
    )

    assert response is None
    [request] = cart.calls("set_delivery_date")
    assert request.url.params["intervalEnd"] == "22:00"


@pytest.mark.parametrize(
    ("captured", "data", "key", "placeholders"),
    [
        (
            PICKUP_CAPTURED,
            {"date": "2026-11-09", "start": "08:00"},
            "pickup_slot_sold_out",
            {"slot": "2026-11-09 08:00-10:00"},
        ),
        (
            HOME_CAPTURED,
            {"date": "2026-11-02", "start": "00:00"},
            "pickup_slot_closed",
            {"slot": "2026-11-02 00:00-06:00"},
        ),
        (
            PICKUP_CAPTURED,
            {"date": "2026-11-09", "start": "09:00"},
            "pickup_slot_not_offered",
            {"date": "2026-11-09", "start": "09:00"},
        ),
        (
            PICKUP_CAPTURED,
            {"date": "2026-11-09", "start": "16:00", "end": "17:00"},
            "pickup_slot_not_offered",
            {"date": "2026-11-09", "start": "16:00"},
        ),
    ],
)
async def test_choose_pickup_slot_refuses_a_slot_that_is_not_open(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
    captured: str,
    data: dict[str, str],
    key: str,
    placeholders: dict[str, str],
) -> None:
    freezer.move_to(captured)
    fixture = "cart_home.json" if captured == HOME_CAPTURED else "cart_logged_in.json"
    bilka.cart_contents = load_fixture(fixture)
    await setup_integration()

    with pytest.raises(ServiceValidationError) as refused:
        await call(hass, "choose_pickup_slot", data)

    assert refused.value.translation_key == key
    assert refused.value.translation_placeholders == placeholders
    assert bilka.calls("set_delivery_date") == []


async def test_choose_pickup_slot_carries_bilkas_refusal(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    await setup_integration()
    cart.script("set_delivery_date", load_fixture("set_delivery_date_unavailable.json"))

    with pytest.raises(ServiceValidationError) as refused:
        await call(hass, "choose_pickup_slot", {"date": "2026-11-09", "start": "16:00"})

    assert refused.value.translation_key == "pickup_slot_unavailable"
    assert refused.value.translation_placeholders == {
        "slot": "2026-11-09 16:00-18:00",
        "error": "Det valgte tidspunkt er ikke tilgængelig",
    }


async def test_choose_pickup_slot_when_bilka_fails(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    await setup_integration()
    cart.script("set_delivery_date", httpx.ConnectError("no route to host"))

    with pytest.raises(HomeAssistantError) as failed:
        await call(hass, "choose_pickup_slot", {"date": "2026-11-09", "start": "16:00"})

    assert not isinstance(failed.value, ServiceValidationError)
    assert failed.value.translation_key == "action_failed"


async def test_choose_pickup_point_returns_what_the_move_changed_and_refreshes(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    cart: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-30T12:00:00+01:00")
    cart.unsold["1662"] = {"75191"}
    await setup_integration()
    polls = len(cart.calls("order_history"))

    response = await call(hass, "choose_pickup_point", {"pickup_point": "Pickup Fredericia"})

    [request] = cart.calls("set_delivery_address")
    assert dict(request.url.params) == {"u": "w", "id": "185582", "zipcode": "7000"}
    assert response == {
        "changed": True,
        "before": {
            "pickup_point": "Bilka Vejle",
            "home_delivery": False,
            "store_id": "1661",
            "total": 127.0,
            "service_fee": 39.0,
            "packaging": 0.0,
            "delivery_fee": 0.0,
        },
        "after": {
            "pickup_point": "Pickup Fredericia",
            "home_delivery": False,
            "store_id": "1662",
            "total": 151.0,
            "service_fee": 39.0,
            "packaging": 5.0,
            "delivery_fee": 19.0,
        },
        "minimum_order": None,
        "unavailable": [
            {"product": "Mini Mix", "article_number": "75191", "quantity": 1, "reason": "Udsolgt"}
        ],
        "available_again": [],
        "gone": [],
        "pickup_slot": {
            "starts_at": "2026-11-01T14:00:00+01:00",
            "ends_at": "2026-11-01T16:00:00+01:00",
        },
        "pickup_slot_closed": True,
    }
    assert len(cart.calls("order_history")) == polls + 1


async def test_choose_home_delivery_returns_the_change_without_the_home_address(
    hass: HomeAssistant, setup_integration: SetupIntegration, home_delivery_cart: FakeBilka
) -> None:
    await setup_integration()
    await call(hass, "get_cart", {})  # remembers the Home Address
    home_delivery_cart.cart_contents = load_fixture("cart_logged_in.json")
    polls = len(home_delivery_cart.calls("order_history"))

    response = await call(hass, "choose_home_delivery", {})

    [request] = home_delivery_cart.calls("set_delivery_address")
    assert dict(request.url.params) == {"u": "w", "id": "185595", "zipcode": "9999"}
    assert response["changed"] is True
    assert response["after"]["home_delivery"] is True
    assert response["after"]["pickup_point"] is None
    assert response["after"]["delivery_fee"] == 39.0
    assert response["minimum_order"] == 500.0
    assert "Kunde Eksempel" not in str(response)
    assert "9999" not in str(response)
    assert len(home_delivery_cart.calls("order_history")) == polls + 1
    for state in hass.states.async_all():
        assert "Kunde Eksempel" not in str(state.as_dict())


async def test_choose_pickup_point_without_a_response(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()

    response = await call(
        hass, "choose_pickup_point", {"pickup_point": "Bilka Kolding"}, return_response=False
    )

    assert response is None
    assert cart.cart_contents["deliveryAddress"]["name"] == "Bilka Kolding"


async def test_choose_pickup_point_refuses_an_unknown_name(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()

    with pytest.raises(ServiceValidationError) as refused:
        await call(hass, "choose_pickup_point", {"pickup_point": "Bilka Atlantis"})

    assert refused.value.translation_key == "unknown_pickup_point"
    assert refused.value.translation_placeholders == {"pickup_point": "Bilka Atlantis"}
    assert cart.calls("set_delivery_address") == []


async def test_choose_home_delivery_without_a_known_home_address(
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
    await setup_integration()

    with pytest.raises(ServiceValidationError) as refused:
        await call(hass, "choose_home_delivery", {})

    assert refused.value.translation_key == "no_home_address"
    assert cart.calls("set_delivery_address") == []


async def test_the_home_address_is_remembered_in_the_entrys_store(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    setup_integration: SetupIntegration,
    home_delivery_cart: FakeBilka,
) -> None:
    entry = await setup_integration()
    await call(hass, "get_cart", {})

    session = hass_storage[f"{DOMAIN}.{entry.entry_id}.session"]["data"]
    assert session["home_address"] == {"id": "185595", "zip_code": "9999"}


@pytest.mark.parametrize("language", ["en", "da"])
async def test_action_texts_are_translated(hass: HomeAssistant, language: str) -> None:
    services = await async_get_translations(hass, language, "services", [DOMAIN])
    exceptions = await async_get_translations(hass, language, "exceptions", [DOMAIN])
    prefix = f"component.{DOMAIN}"

    for action, fields in [
        ("add_to_cart", ["config_entry_id", "article_number", "quantity"]),
        ("add_items_to_cart", ["config_entry_id", "items"]),
        ("remove_from_cart", ["config_entry_id", "article_number", "quantity"]),
        (
            "search",
            ["config_entry_id", "query", "limit", "in_stock", "on_offer", "in_leaflet", "sort"],
        ),
        ("get_cart", ["config_entry_id"]),
        ("choose_pickup_slot", ["config_entry_id", "date", "start", "end"]),
        ("choose_pickup_point", ["config_entry_id", "pickup_point"]),
        ("choose_home_delivery", ["config_entry_id"]),
    ]:
        assert services[f"{prefix}.services.{action}.name"]
        assert services[f"{prefix}.services.{action}.description"]
        for field in fields:
            assert services[f"{prefix}.services.{action}.fields.{field}.name"]
            assert services[f"{prefix}.services.{action}.fields.{field}.description"]
    for key in [
        "entry_not_found",
        "entry_not_loaded",
        "no_entry_loaded",
        "entry_required",
        "no_pickup_point",
        "no_home_address",
        "action_failed",
    ]:
        assert exceptions[f"{prefix}.exceptions.{key}.message"]
    assert "{error}" in exceptions[f"{prefix}.exceptions.no_items_added.message"]
    assert "{error}" in exceptions[f"{prefix}.exceptions.cart_change_rejected.message"]
    for key, placeholders in [
        ("pickup_slot_sold_out", ["{slot}"]),
        ("pickup_slot_closed", ["{slot}"]),
        ("pickup_slot_unavailable", ["{slot}", "{error}"]),
        ("pickup_slot_not_offered", ["{date}", "{start}"]),
        ("pickup_slot_ambiguous", ["{date}", "{start}"]),
        ("unknown_pickup_point", ["{pickup_point}"]),
    ]:
        message = exceptions[f"{prefix}.exceptions.{key}.message"]
        assert all(placeholder in message for placeholder in placeholders)
