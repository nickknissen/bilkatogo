from collections.abc import Awaitable, Callable
from typing import Any

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from bilkatogo import (
    BilkaToGoClient,
    BilkaToGoError,
    Cart,
    CartChangeRejectedError,
    InvalidCredentialsError,
    NoPickupPointError,
    Product,
)

from .const import DOMAIN
from .coordinator import BilkaToGoConfigEntry

SERVICE_ADD_TO_CART = "add_to_cart"
SERVICE_REMOVE_FROM_CART = "remove_from_cart"
SERVICE_SEARCH = "search"
SERVICE_GET_CART = "get_cart"

ATTR_ARTICLE_NUMBER = "article_number"
ATTR_QUANTITY = "quantity"
ATTR_QUERY = "query"
ATTR_LIMIT = "limit"

DEFAULT_SEARCH_LIMIT = 10

ADD_TO_CART_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_ARTICLE_NUMBER): cv.string,
        probatio.Optional(ATTR_QUANTITY, default=1): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1)
        ),
    }
)

# Without a quantity, the whole Cart Line goes.
REMOVE_FROM_CART_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_ARTICLE_NUMBER): cv.string,
        probatio.Optional(ATTR_QUANTITY): probatio.All(probatio.Coerce(int), probatio.Range(min=1)),
    }
)

SEARCH_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_QUERY): cv.string,
        probatio.Optional(ATTR_LIMIT, default=DEFAULT_SEARCH_LIMIT): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1)
        ),
    }
)

GET_CART_SCHEMA = probatio.Schema({probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


def _entry(call: ServiceCall) -> BilkaToGoConfigEntry:
    hass = call.hass
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"title": entry.title},
            )
        return entry
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_entry_loaded")
    if len(entries) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_required")
    return entries[0]


async def _run[T](call: ServiceCall, action: Callable[[BilkaToGoClient], Awaitable[T]]) -> T:
    entry = _entry(call)
    try:
        return await action(entry.runtime_data.client)
    except InvalidCredentialsError as err:
        entry.async_start_reauth(call.hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="auth_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    except CartChangeRejectedError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cart_change_rejected",
            translation_placeholders={"error": str(err)},
        ) from err
    except NoPickupPointError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="no_pickup_point"
        ) from err
    except BilkaToGoError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="action_failed",
            translation_placeholders={"error": str(err)},
        ) from err


def _cart_response(cart: Cart) -> dict[str, Any]:
    return {
        "lines": [
            {
                "product": line.product,
                "article_number": line.article_number,
                "quantity": line.quantity,
                "price": float(line.price),
                "line_total": float(line.line_total),
            }
            for line in cart.lines
        ],
        "total": float(cart.total),
        "deposit": float(cart.deposit),
        "service_fee": float(cart.service_fee),
        "packaging": float(cart.packaging),
        "discount": float(cart.discount),
        "pickup_point": cart.pickup_point.name if cart.pickup_point else None,
        "warnings": list(cart.warnings),
    }


def _product_response(product: Product) -> dict[str, Any]:
    offer = product.offer
    return {
        "article_number": product.article_number,
        "name": product.name,
        "brand": product.brand,
        "price": float(product.price),
        "unit_price": float(product.unit_price) if product.unit_price is not None else None,
        "unit": product.unit,
        "in_stock": product.in_stock,
        "offer": {
            "label": offer.label,
            "price": float(offer.price),
            "unit_price": float(offer.unit_price) if offer.unit_price is not None else None,
            "ends": offer.ends.isoformat() if offer.ends else None,
            "limit_per_customer": offer.limit_per_customer,
        }
        if offer
        else None,
        "multibuy_offer": product.multibuy_offer,
    }


async def _add_to_cart(call: ServiceCall) -> ServiceResponse:
    cart = await _run(
        call,
        lambda client: client.add_to_cart(call.data[ATTR_ARTICLE_NUMBER], call.data[ATTR_QUANTITY]),
    )
    return _cart_response(cart) if call.return_response else None


async def _remove_from_cart(call: ServiceCall) -> ServiceResponse:
    cart = await _run(
        call,
        lambda client: client.remove_from_cart(
            call.data[ATTR_ARTICLE_NUMBER], call.data.get(ATTR_QUANTITY)
        ),
    )
    return _cart_response(cart) if call.return_response else None


async def _search(call: ServiceCall) -> ServiceResponse:
    products = await _run(
        call, lambda client: client.search(call.data[ATTR_QUERY], limit=call.data[ATTR_LIMIT])
    )
    return {"products": [_product_response(product) for product in products]}


async def _get_cart(call: ServiceCall) -> ServiceResponse:
    return _cart_response(await _run(call, lambda client: client.get_cart()))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_TO_CART,
        _add_to_cart,
        schema=ADD_TO_CART_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_FROM_CART,
        _remove_from_cart,
        schema=REMOVE_FROM_CART_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        _search,
        schema=SEARCH_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_CART,
        _get_cart,
        schema=GET_CART_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
