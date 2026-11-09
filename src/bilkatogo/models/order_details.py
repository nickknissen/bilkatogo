from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .base import BilkaDataClass
from .money import discount, kroner
from .order import OrderStatus, PickupPoint, PickupSlot
from .product import size_of


@dataclass
class PickupCode(BilkaDataClass):
    code: str
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PickupCode | None:
        code = data.get("order_pickup_code")
        if not code:
            return None
        return cls(_raw={"order_pickup_code": code}, code=str(code))


_LOGGER = logging.getLogger(__name__)

SUBSTITUTED_TAGLINE = "erstattet"

# The Missing Item shape is inferred from a synthetic fixture. Every Missing Item is logged with
# its raw line until a real capture confirms the shape; then only the unclassified log stays.
MISSING_ITEM_SHAPE_ISSUE = "https://github.com/nickknissen/bilkatogo/issues/27"


@dataclass
class Substitution(BilkaDataClass):
    original_product: str
    original_article_number: str
    original_size: str
    ordered_quantity: int
    replacement_product: str
    replacement_article_number: str
    replacement_size: str
    quantity: int
    replacement_price: Decimal
    replacement_line_total: Decimal
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Substitution:
        """Build from an Order Line that `_classify` found to be a Substitution.

        The Replacement Product's line total is what the customer pays for it, which can be
        less than its Price: Bilka charges the Original Product's price for a dearer one.
        """
        replacement = data["children"][0]
        return cls(
            _raw=data,
            original_product=_product_name(data),
            original_article_number=_article_number(data),
            original_size=_size(data),
            ordered_quantity=int(data.get("quantity") or 0),
            replacement_product=_product_name(replacement),
            replacement_article_number=_article_number(replacement),
            replacement_size=_size(replacement),
            quantity=int(replacement.get("quantity") or 0),
            replacement_price=kroner((replacement.get("product") or {}).get("price")),
            replacement_line_total=kroner(replacement.get("amount")),
        )


@dataclass
class MissingItem(BilkaDataClass):
    product: str
    article_number: str
    ordered_quantity: int
    missing_quantity: int
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissingItem:
        """Build from an Order Line that `_classify` found to be a Missing Item."""
        ordered = int(data.get("quantity") or 0)
        missing = _quantity(data.get("unavailable_quantity"))
        return cls(
            _raw=data,
            product=_product_name(data),
            article_number=_article_number(data),
            ordered_quantity=ordered,
            missing_quantity=missing if 0 < missing < ordered else ordered,
        )


@dataclass
class OrderLine(BilkaDataClass):
    category: str
    article_number: str
    name: str
    quantity: int
    amount: Decimal
    substituted: bool
    unavailable_reason: str
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], category: str = "", *, substituted: bool = False
    ) -> OrderLine:
        return cls(
            _raw=data,
            category=category,
            article_number=_article_number(data),
            name=_product_name(data),
            quantity=int(data.get("quantity") or 0),
            amount=kroner(data.get("amount")),
            substituted=substituted,
            unavailable_reason=data.get("unavailable_reason") or "",
        )


@dataclass
class OrderDetails(BilkaDataClass):
    order_number: str
    status: OrderStatus
    status_code: str
    pickup_slot: PickupSlot | None
    pickup_point: PickupPoint | None
    home_delivery: bool
    pickup_code: PickupCode | None
    order_total: Decimal
    ordered_total: Decimal | None
    deposit: Decimal
    service_fee: Decimal
    packaging: Decimal
    delivery_fee: Decimal
    discount: Decimal
    lines: list[OrderLine]
    substitutions: list[Substitution]
    missing_items: list[MissingItem]
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], ordered_total: Decimal | None = None) -> OrderDetails:
        status_code = data.get("status") or ""
        address = data.get("deliveryAddress")
        stat = data.get("stat") or {}
        raw_lines = [
            (group.get("headline") or "", line)
            for group in data.get("lines") or []
            for block in group.get("lines") or []
            for line in block.get("orderlines") or []
        ]
        order_number = str(data["orderId"])
        lines: list[OrderLine] = []
        substitutions: list[Substitution] = []
        missing_items: list[MissingItem] = []
        for category, raw_line in raw_lines:
            kind = _classify(raw_line)
            if isinstance(kind, Substitution):
                substitutions.append(kind)
            elif isinstance(kind, MissingItem):
                missing_items.append(kind)
                _LOGGER.warning(
                    "Order %s: %s is a Missing Item whose shape is not yet confirmed; "
                    "please share this line on %s: %s",
                    order_number,
                    kind.product,
                    MISSING_ITEM_SHAPE_ISSUE,
                    _json(raw_line),
                )
            elif raw_line.get("unavailable_reason"):
                _LOGGER.warning(
                    "Order %s: %s is unavailable (%s) but neither a Substitution nor a "
                    "Missing Item: %s",
                    order_number,
                    _product_name(raw_line),
                    raw_line.get("unavailable_reason"),
                    _json(raw_line),
                )
            substituted = isinstance(kind, Substitution)
            lines.append(OrderLine.from_dict(raw_line, category, substituted=substituted))
        return cls(
            _raw=data,
            order_number=order_number,
            status=OrderStatus.from_code(status_code),
            status_code=status_code,
            pickup_slot=PickupSlot.from_dict(data),
            pickup_point=PickupPoint.from_order(address) if address else None,
            home_delivery=bool((address or {}).get("home_delivery", False)),
            pickup_code=PickupCode.from_dict(address or {}),
            order_total=kroner(data.get("price")),
            ordered_total=ordered_total,
            deposit=kroner(stat.get("deposit")),
            service_fee=kroner(stat.get("packing")),
            packaging=kroner(stat.get("packaging_price")),
            delivery_fee=kroner(stat.get("delivery_price")),
            discount=discount(stat),
            lines=lines,
            substitutions=substitutions,
            missing_items=missing_items,
        )


def _classify(data: dict[str, Any]) -> Substitution | MissingItem | None:
    """Decide once what an Order Line is: a Substitution, a Missing Item, or neither.

    A Substitution is struck through, tagged "erstattet" and has its Replacement Product as
    a child. A Missing Item is unavailable with no Replacement Product and no child.
    """
    children = data.get("children")
    if data.get("strikethrough") and data.get("tagline") == SUBSTITUTED_TAGLINE and children:
        return Substitution.from_dict(data)
    if data.get("unavailable_reason") and not data.get("replacement") and not children:
        return MissingItem.from_dict(data)
    return None


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _product_name(data: dict[str, Any]) -> str:
    return (data.get("product") or {}).get("name") or ""


def _article_number(data: dict[str, Any]) -> str:
    return str((data.get("product") or {}).get("objectID") or "")


def _size(data: dict[str, Any]) -> str:
    return size_of(data.get("product") or {}) or ""


def _quantity(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
