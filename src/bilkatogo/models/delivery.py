from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .base import BilkaDataClass
from .cart import Cart, CartLine, cart_store_id
from .money import kroner


@dataclass
class HomeAddress(BilkaDataClass):
    """Where Home Delivery brings the Cart: a zip code and a name, served by one Store. Bilka
    holds no street until checkout. The client remembers it by `id` and zip code only, so a
    remembered Home Address has no name, Store or Minimum Order."""

    id: str
    zip_code: str
    recipient_name: str | None = None
    store_id: str | None = None
    minimum_order: Decimal | None = None
    _raw: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_delivery_address(cls, data: dict[str, Any]) -> HomeAddress:
        """From the Cart's `deliveryAddress`, or an `AvailableDeliveryAddresses` entry, that is
        a Home Address. Bilka keeps the zip code typed in for Home Delivery in
        `temporary_zipcode`; `zip` held the same in every capture."""
        return cls(
            _raw=data,
            id=str(data.get("id") or ""),
            zip_code=str(data.get("temporary_zipcode") or data.get("zip") or ""),
            recipient_name=data.get("recipient_name") or None,
            store_id=cart_store_id(data),
            minimum_order=kroner(data.get("minimum_buy")) or None,
        )


@dataclass
class DeliveryChange(BilkaDataClass):
    """What moving the Cart to another Pickup Point or to Home Delivery changed, from the Cart
    read before and after. Bilka keeps the Cart Lines the new place can't supply and marks them
    unavailable; it adds and removes the Delivery Fee and Packaging itself."""

    before: Cart
    after: Cart
    changed: bool
    """False when the Cart was there already and nothing was sent to Bilka."""
    unavailable: list[CartLine]
    """Cart Lines that became unavailable, each with Bilka's `unavailable_reason`."""
    available_again: list[CartLine]
    """Cart Lines that were unavailable before and are available now."""
    gone: list[CartLine]
    """Cart Lines from before that are no longer in the Cart; empty in every capture."""
    pickup_slot_closed: bool
    """Whether the chosen Pickup Slot is closed where the Cart is now; choose another."""

    @classmethod
    def between(cls, before: Cart, after: Cart, *, changed: bool = True) -> DeliveryChange:
        was = {line.article_number: line for line in before.lines}
        now = {line.article_number: line for line in after.lines}
        return cls(
            before=before,
            after=after,
            changed=changed,
            unavailable=[
                line
                for number, line in now.items()
                if not line.available and (number not in was or was[number].available)
            ],
            available_again=[
                line
                for number, line in now.items()
                if line.available and number in was and not was[number].available
            ],
            gone=[line for number, line in was.items() if number not in now],
            pickup_slot_closed=after.pickup_slot_closed,
        )
