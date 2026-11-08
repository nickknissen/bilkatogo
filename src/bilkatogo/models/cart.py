import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ..const import BILKA_TIME_ZONE
from .base import BilkaDataClass
from .money import discount, kroner
from .order import PickupPoint, PickupSlot

FEE_GROUPS = frozenset({"Emballage", "Services"})


@dataclass
class CartLine(BilkaDataClass):
    product: str
    article_number: str
    quantity: int
    price: Decimal
    line_total: Decimal
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CartLine:
        product = data.get("product") or {}
        return cls(
            _raw=data,
            product=product.get("name") or "",
            article_number=str(product.get("objectID") or ""),
            quantity=int(data.get("quantity") or 0),
            price=kroner(product.get("price")),
            line_total=kroner(data.get("amount")),
        )


@dataclass
class Cart(BilkaDataClass):
    lines: list[CartLine]
    total: Decimal
    deposit: Decimal
    service_fee: Decimal
    packaging: Decimal
    discount: Decimal
    pickup_point: PickupPoint | None
    """None for Home Delivery or when the Cart has no delivery address."""
    home_delivery: bool
    store_id: str | None
    """The Store that serves the Cart, for pickup and Home Delivery alike."""
    delivery_fee: Decimal
    minimum_order: Decimal | None
    """The Minimum Order; None when Bilka sets none, as for pickup."""
    minimum_left: Decimal
    """How much more the Cart needs to reach the Minimum Order; 0 when it does."""
    next_available_date: date | None
    pickup_slot: PickupSlot | None
    """The Pickup Slot chosen in the Cart, while it lies ahead. Bilka keeps the last Order's
    slot in the Cart for weeks after it ended."""
    warnings: list[str] = field(default_factory=list)
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cart:
        stat = data.get("stat") or {}
        address = data.get("deliveryAddress") or {}
        return cls(
            _raw=data,
            lines=[
                CartLine.from_dict(line)
                for group in data.get("lines") or []
                if group.get("headline") not in FEE_GROUPS
                for block in group.get("lines") or []
                for line in block.get("orderlines") or []
            ],
            total=kroner(stat.get("price")),
            deposit=kroner(stat.get("deposit")),
            service_fee=kroner(stat.get("packing")),
            packaging=kroner(stat.get("packaging_price")),
            discount=discount(stat),
            pickup_point=cart_pickup_point(address),
            home_delivery=is_home_delivery(address),
            store_id=cart_store_id(address),
            delivery_fee=kroner(stat.get("delivery_price")),
            minimum_order=kroner(address.get("minimum_buy")) or None,
            minimum_left=kroner(stat.get("minimum_left")),
            next_available_date=_date(address.get("nextAvailableDeliveryDate")),
            pickup_slot=upcoming_cart_slot(data.get("deliveryDate")),
            warnings=_cart_warnings(data, stat),
        )


def _cart_warnings(data: dict[str, Any], stat: dict[str, Any]) -> list[str]:
    """Bilka's notices on the Cart: how far it is from the Minimum Order, then its messages."""
    warnings = []
    minimum_text = re.sub(r"<[^>]+>", "", stat.get("minimum_left_text") or "").strip()
    if kroner(stat.get("minimum_left")) > 0 and minimum_text:
        warnings.append(minimum_text)
    for message in data.get("messages") or []:
        text = message.get("text") if isinstance(message, dict) else None
        warnings.append(str(text) if text else str(message))
    return warnings


def upcoming_cart_slot(data: dict[str, Any] | None) -> PickupSlot | None:
    """The Pickup Slot chosen in the Cart from `deliveryDate`, `intervalStart` and
    `intervalEnd`, while it lies ahead."""
    slot = PickupSlot.from_cart(data) if data else None
    if slot is None or slot.ends_at <= datetime.now(BILKA_TIME_ZONE):
        return None
    return slot


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value)
    except TypeError, ValueError:
        return None


def is_home_delivery(address: dict[str, Any] | None) -> bool:
    """Whether the Cart's `deliveryAddress` is the customer's home rather than a Pickup Point.

    Not by a name starting with "Bilka": standalone Pickup Points are named e.g. "Pickup
    Fredericia".
    """
    return bool(address and (address.get("dawa_uid") or address.get("recipient_name")))


def cart_store_id(address: dict[str, Any] | None) -> str | None:
    store_id = (address or {}).get("sid")
    return str(store_id) if store_id else None


def cart_pickup_point(address: dict[str, Any] | None) -> PickupPoint | None:
    if not address or is_home_delivery(address) or cart_store_id(address) is None:
        return None
    return PickupPoint.from_delivery_address(address)


class CartChangeStatus(StrEnum):
    APPLIED = "applied"
    """Bilka set the quantity asked for; `message` holds any warning it gave."""
    REJECTED = "rejected"
    """Bilka refused the change or set a different quantity; `message` is Bilka's."""
    SKIPPED = "skipped"
    """Not made because Bilka stopped answering; the Cart shows what it holds."""


@dataclass
class CartChange(BilkaDataClass):
    """The outcome of setting one Product's quantity in the Cart."""

    article_number: str
    requested: int
    """The quantity asked for in the Cart, after adding for `add_items_to_cart`."""
    applied: int | None
    """The quantity Bilka set, None when it set none or did not say."""
    status: CartChangeStatus
    message: str | None


@dataclass
class CartChanges(BilkaDataClass):
    """Several Cart changes made one after another, with the Cart read once afterwards."""

    changes: list[CartChange]
    cart: Cart
