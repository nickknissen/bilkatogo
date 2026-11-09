from collections.abc import Awaitable, Callable
from datetime import date, time
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
    CartChangeStatus,
    CartLine,
    ChosenPickupSlot,
    DeliveryChange,
    InvalidCredentialsError,
    NoHomeAddressError,
    NoPickupPointError,
    PickupSlot,
    PickupSlotUnavailableError,
    Product,
    SearchSort,
    SlotAvailability,
    UnknownPickupPointError,
)
from bilkatogo.const import CHOOSABLE_SLOT_DAYS

from .const import DOMAIN
from .coordinator import BilkaToGoConfigEntry

SERVICE_ADD_TO_CART = "add_to_cart"
SERVICE_ADD_ITEMS_TO_CART = "add_items_to_cart"
SERVICE_REMOVE_FROM_CART = "remove_from_cart"
SERVICE_SEARCH = "search"
SERVICE_GET_CART = "get_cart"
SERVICE_CHOOSE_PICKUP_SLOT = "choose_pickup_slot"
SERVICE_CHOOSE_PICKUP_POINT = "choose_pickup_point"
SERVICE_CHOOSE_HOME_DELIVERY = "choose_home_delivery"

ATTR_ARTICLE_NUMBER = "article_number"
ATTR_QUANTITY = "quantity"
ATTR_QUERY = "query"
ATTR_LIMIT = "limit"
ATTR_ITEMS = "items"
ATTR_IN_STOCK = "in_stock"
ATTR_ON_OFFER = "on_offer"
ATTR_IN_LEAFLET = "in_leaflet"
ATTR_SORT = "sort"
ATTR_DATE = "date"
ATTR_START = "start"
ATTR_END = "end"
ATTR_PICKUP_POINT = "pickup_point"

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
        probatio.Optional(ATTR_IN_STOCK, default=False): cv.boolean,
        probatio.Optional(ATTR_ON_OFFER, default=False): cv.boolean,
        probatio.Optional(ATTR_IN_LEAFLET, default=False): cv.boolean,
        probatio.Optional(ATTR_SORT, default=SearchSort.RELEVANCE): probatio.Coerce(SearchSort),
    }
)

ITEM_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_ARTICLE_NUMBER): cv.string,
        probatio.Optional(ATTR_QUANTITY, default=1): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1)
        ),
    }
)

ADD_ITEMS_TO_CART_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_ITEMS): probatio.All(
            cv.ensure_list, probatio.Length(min=1), [ITEM_SCHEMA]
        ),
    }
)

GET_CART_SCHEMA = probatio.Schema({probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})

# `end` is only needed when two Pickup Slots on the date start at the same time.
CHOOSE_PICKUP_SLOT_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_DATE): cv.date,
        probatio.Required(ATTR_START): cv.time,
        probatio.Optional(ATTR_END): cv.time,
    }
)

CHOOSE_PICKUP_POINT_SCHEMA = probatio.Schema(
    {
        probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Required(ATTR_PICKUP_POINT): cv.string,
    }
)

CHOOSE_HOME_DELIVERY_SCHEMA = probatio.Schema({probatio.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})

UNAVAILABLE_SLOT_KEYS = {
    SlotAvailability.SOLD_OUT: "pickup_slot_sold_out",
    SlotAvailability.CLOSED: "pickup_slot_closed",
}


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
    except PickupSlotUnavailableError as err:
        raise _unavailable_slot_error(err) from err
    except NoPickupPointError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="no_pickup_point"
        ) from err
    except UnknownPickupPointError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_pickup_point",
            translation_placeholders={"pickup_point": err.name},
        ) from err
    except NoHomeAddressError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_home_address"
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
        "delivery_fee": float(cart.delivery_fee),
        "home_delivery": cart.home_delivery,
        # A Home Delivery Cart has no Pickup Point, so the home address never shows here.
        "pickup_point": cart.pickup_point.name if cart.pickup_point else None,
        "pickup_slot": _slot_response(cart.pickup_slot),
        "minimum_order": float(cart.minimum_order) if cart.minimum_order is not None else None,
        "minimum_left": float(cart.minimum_left),
        "next_available_date": cart.next_available_date.isoformat()
        if cart.next_available_date
        else None,
        "warnings": list(cart.warnings),
    }


def _not_offered(day: date, start: time) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="pickup_slot_not_offered",
        translation_placeholders={"date": day.isoformat(), "start": f"{start:%H:%M}"},
    )


def _unavailable_slot_error(err: PickupSlotUnavailableError) -> ServiceValidationError:
    """Listed as closed or sold out, refused by Bilka with its own message, or not offered."""
    slot = err.slot
    if err.availability in UNAVAILABLE_SLOT_KEYS:
        key = UNAVAILABLE_SLOT_KEYS[err.availability]
        placeholders = {"slot": str(slot)}
    elif err.refused:
        key = "pickup_slot_unavailable"
        placeholders = {"slot": str(slot), "error": err.reason}
    else:
        return _not_offered(slot.date, slot.start)
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
    )


def _slot_response(slot: PickupSlot | None) -> dict[str, Any] | None:
    if slot is None:
        return None
    return {"starts_at": slot.starts_at.isoformat(), "ends_at": slot.ends_at.isoformat()}


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
        "deliverable": product.deliverable,
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


async def _add_items_to_cart(call: ServiceCall) -> ServiceResponse:
    """Adds each item in turn. Items Bilka rejects or skips are in the response; the action
    fails only when none was added."""
    items: dict[str, int] = {}
    for item in call.data[ATTR_ITEMS]:
        article_number = item[ATTR_ARTICLE_NUMBER]
        items[article_number] = items.get(article_number, 0) + item[ATTR_QUANTITY]
    result = await _run(call, lambda client: client.add_items_to_cart(items))
    if all(change.status is not CartChangeStatus.APPLIED for change in result.changes):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="no_items_added",
            translation_placeholders={
                "error": "; ".join(
                    f"{change.article_number}: {change.message or change.status.value}"
                    for change in result.changes
                )
            },
        )
    if not call.return_response:
        return None
    return {
        "changes": [
            {
                "article_number": change.article_number,
                "requested": change.requested,
                "applied": change.applied,
                "status": change.status.value,
                "message": change.message,
            }
            for change in result.changes
        ],
        "cart": _cart_response(result.cart),
    }


async def _search(call: ServiceCall) -> ServiceResponse:
    products = await _run(
        call,
        lambda client: client.search(
            call.data[ATTR_QUERY],
            limit=call.data[ATTR_LIMIT],
            in_stock=call.data[ATTR_IN_STOCK],
            on_offer=call.data[ATTR_ON_OFFER],
            in_leaflet=call.data[ATTR_IN_LEAFLET],
            sort=call.data[ATTR_SORT],
        ),
    )
    return {"products": [_product_response(product) for product in products]}


async def _get_cart(call: ServiceCall) -> ServiceResponse:
    return _cart_response(await _run(call, lambda client: client.get_cart()))


async def _choose_pickup_slot(call: ServiceCall) -> ServiceResponse:
    """Chooses the Cart's Pickup Slot by its date and start, then refreshes the entities."""
    day: date = call.data[ATTR_DATE]
    start: time = call.data[ATTR_START]
    end: time | None = call.data.get(ATTR_END)

    async def choose(client: BilkaToGoClient) -> ChosenPickupSlot:
        if end is not None:
            return await client.choose_pickup_slot(PickupSlot(day, start, end))
        slots = await client.get_pickup_slots(days=CHOOSABLE_SLOT_DAYS)
        matches = slots.starting_at(day, start)
        if not matches:
            raise _not_offered(day, start)
        if len(matches) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="pickup_slot_ambiguous",
                translation_placeholders={"date": day.isoformat(), "start": f"{start:%H:%M}"},
            )
        return await client.choose_pickup_slot(matches[0].slot)

    result = await _run(call, choose)
    # The Next available pickup slot sensor and the Cart's slot follow the change.
    await _entry(call).runtime_data.async_request_refresh()
    if not call.return_response:
        return None
    return {
        "chosen": _slot_response(result.chosen),
        "replaced": _slot_response(result.replaced),
        "changed": result.changed,
    }


def _place_response(cart: Cart) -> dict[str, Any]:
    """Where the Cart is received, by its Pickup Point's name or as Home Delivery: never the
    Home Address."""
    return {
        "pickup_point": cart.pickup_point.name if cart.pickup_point else None,
        "home_delivery": cart.home_delivery,
        "store_id": cart.store_id,
        "total": float(cart.total),
        "service_fee": float(cart.service_fee),
        "packaging": float(cart.packaging),
        "delivery_fee": float(cart.delivery_fee),
    }


def _line_response(line: CartLine) -> dict[str, Any]:
    return {
        "product": line.product,
        "article_number": line.article_number,
        "quantity": line.quantity,
    }


def _delivery_change_response(change: DeliveryChange) -> dict[str, Any]:
    after = change.after
    return {
        "changed": change.changed,
        "before": _place_response(change.before),
        "after": _place_response(after),
        "minimum_order": float(after.minimum_order) if after.minimum_order is not None else None,
        "unavailable": [
            {**_line_response(line), "reason": line.unavailable_reason}
            for line in change.unavailable
        ],
        "available_again": [_line_response(line) for line in change.available_again],
        "gone": [_line_response(line) for line in change.gone],
        "pickup_slot": _slot_response(after.pickup_slot),
        "pickup_slot_closed": change.pickup_slot_closed,
    }


async def _move_cart(
    call: ServiceCall, move: Callable[[BilkaToGoClient], Awaitable[DeliveryChange]]
) -> ServiceResponse:
    change = await _run(call, move)
    # The Store, the Pickup Point and the Pickup Slots follow the Cart.
    await _entry(call).runtime_data.async_request_refresh()
    return _delivery_change_response(change) if call.return_response else None


async def _choose_pickup_point(call: ServiceCall) -> ServiceResponse:
    """Moves the Cart to a Pickup Point by name, then refreshes the entities."""
    name: str = call.data[ATTR_PICKUP_POINT]
    return await _move_cart(call, lambda client: client.choose_pickup_point(name))


async def _choose_home_delivery(call: ServiceCall) -> ServiceResponse:
    """Moves the Cart to Home Delivery at the remembered Home Address, then refreshes the
    entities."""
    return await _move_cart(call, lambda client: client.choose_home_delivery())


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
        SERVICE_ADD_ITEMS_TO_CART,
        _add_items_to_cart,
        schema=ADD_ITEMS_TO_CART_SCHEMA,
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
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHOOSE_PICKUP_SLOT,
        _choose_pickup_slot,
        schema=CHOOSE_PICKUP_SLOT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHOOSE_PICKUP_POINT,
        _choose_pickup_point,
        schema=CHOOSE_PICKUP_POINT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHOOSE_HOME_DELIVERY,
        _choose_home_delivery,
        schema=CHOOSE_HOME_DELIVERY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
