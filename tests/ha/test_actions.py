from typing import Any

import httpx
import probatio
import pytest
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
        "pickup_point": "Bilka Vejle",
        "warnings": [],
    }


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
    hass: HomeAssistant, setup_integration: SetupIntegration, cart: FakeBilka
) -> None:
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
        "pickup_point": "Bilka Vejle",
        "warnings": [],
    }


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


@pytest.mark.parametrize("language", ["en", "da"])
async def test_action_texts_are_translated(hass: HomeAssistant, language: str) -> None:
    services = await async_get_translations(hass, language, "services", [DOMAIN])
    exceptions = await async_get_translations(hass, language, "exceptions", [DOMAIN])
    prefix = f"component.{DOMAIN}"

    for action, fields in [
        ("add_to_cart", ["config_entry_id", "article_number", "quantity"]),
        ("remove_from_cart", ["config_entry_id", "article_number", "quantity"]),
        ("search", ["config_entry_id", "query", "limit"]),
        ("get_cart", ["config_entry_id"]),
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
        "action_failed",
    ]:
        assert exceptions[f"{prefix}.exceptions.{key}.message"]
    assert "{error}" in exceptions[f"{prefix}.exceptions.cart_change_rejected.message"]
