from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .base import BilkaDataClass
from .money import discount, kroner
from .order import PickupPoint

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
    warnings: list[str] = field(default_factory=list)
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cart:
        stat = data.get("stat") or {}
        address = data.get("deliveryAddress")
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
            pickup_point=PickupPoint.from_delivery_address(address) if address else None,
        )


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
