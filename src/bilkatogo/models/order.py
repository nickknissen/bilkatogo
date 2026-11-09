from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ..const import BILKA_TIME_ZONE
from .base import BilkaDataClass
from .money import kroner


class OrderStatus(StrEnum):
    PROCESSING = "processing"
    RECEIVED = "received"
    PACKED = "packed"
    PICKED_UP = "picked_up"
    NOT_COLLECTED = "not_collected"
    CANCELLED = "cancelled"
    SOLD_OUT = "sold_out"
    UNKNOWN = "unknown"

    @classmethod
    def from_code(cls, code: str) -> OrderStatus:
        return _STATUS_BY_CODE.get(code.strip().replace(" ", "_").upper(), cls.UNKNOWN)


_STATUS_BY_CODE = {
    "UBEHANDLET": OrderStatus.PROCESSING,
    "CAPTURE_IGANG": OrderStatus.PROCESSING,
    "BESTILT": OrderStatus.RECEIVED,
    "PAKKET": OrderStatus.PACKED,
    "LEVERET": OrderStatus.PICKED_UP,
    "FAKTURERET": OrderStatus.PICKED_UP,
    "UAFHENTET": OrderStatus.NOT_COLLECTED,
    "ANNULLERET": OrderStatus.CANCELLED,
    "FULDT_UDSOLGT": OrderStatus.SOLD_OUT,
}


@dataclass
class PickupSlot(BilkaDataClass):
    date: date
    start: time
    end: time
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    def __str__(self) -> str:
        return f"{self.date.isoformat()} {self.start:%H:%M}-{self.end:%H:%M}"

    @property
    def starts_at(self) -> datetime:
        return datetime.combine(self.date, self.start, BILKA_TIME_ZONE)

    @property
    def ends_at(self) -> datetime:
        """An end at or before the start, e.g. 20:00 to 06:00, is on the next day."""
        day = self.date + timedelta(days=1) if self.end <= self.start else self.date
        return datetime.combine(day, self.end, BILKA_TIME_ZONE)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PickupSlot | None:
        interval = data.get("deliveryInterval") or {}
        try:
            return cls(
                _raw={"deliveryDate": data.get("deliveryDate"), "deliveryInterval": interval},
                date=date.fromisoformat(data["deliveryDate"]),
                start=time.fromisoformat(interval["start"]),
                end=time.fromisoformat(interval["end"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_cart(cls, data: dict[str, Any]) -> PickupSlot | None:
        """The Pickup Slot in the Cart's `deliveryDate`, or at the top of `SetDeliveryDate`'s
        answer, which has the same shape; None when any part is missing."""
        try:
            return cls(
                _raw=data,
                date=date.fromisoformat(data["deliveryDate"]),
                start=time.fromisoformat(data["intervalStart"]),
                end=time.fromisoformat(data["intervalEnd"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class PickupPoint(BilkaDataClass):
    name: str
    address: str
    zip_code: str
    city: str
    store_id: str | None = None
    id: str | None = None
    """Bilka's id for the place, which the Cart is moved to it by; None on an Order."""
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_order(cls, data: dict[str, Any]) -> PickupPoint:
        return cls(
            _raw=data,
            name=data.get("location") or "",
            address=data.get("address") or "",
            zip_code=str(data.get("zipCode") or ""),
            city=data.get("city") or "",
        )

    @classmethod
    def from_delivery_address(cls, data: dict[str, Any]) -> PickupPoint:
        store_id = data.get("sid")
        return cls(
            _raw=data,
            name=(data.get("name") or "").strip(),
            address=(data.get("street") or "").strip(),
            zip_code=str(data.get("zip") or ""),
            city=data.get("city") or "",
            store_id=str(store_id) if store_id else None,
            id=str(data["id"]) if data.get("id") else None,
        )


@dataclass
class Order(BilkaDataClass):
    order_number: str
    status: OrderStatus
    status_code: str
    ordered_at: datetime | None
    pickup_slot: PickupSlot | None
    pickup_point: PickupPoint | None
    home_delivery: bool
    delivered: bool
    ordered_total: Decimal
    order_total: Decimal
    content: str
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Order:
        status_code = data.get("status") or ""
        pickupzone = data.get("pickupzone")
        return cls(
            _raw=data,
            order_number=str(data["orderId"]),
            status=OrderStatus.from_code(status_code),
            status_code=status_code,
            ordered_at=_ordered_at(data.get("ordered")),
            pickup_slot=PickupSlot.from_dict(data),
            pickup_point=PickupPoint.from_order(pickupzone) if pickupzone else None,
            home_delivery=bool((pickupzone or {}).get("home_delivery", False)),
            delivered=bool(data.get("delivered", False)),
            ordered_total=kroner(data.get("orderedPrice")),
            order_total=kroner(data.get("packedPrice")),
            content=data.get("content") or "",
        )

    @property
    def finished(self) -> bool:
        """Nothing more will happen to this Order."""
        return self.status in _FINISHED_STATUSES


def _ordered_at(ordered: dict[str, Any] | None) -> datetime | None:
    if not ordered:
        return None
    try:
        ordered_at = datetime.fromisoformat(
            f"{ordered['date']}T{ordered.get('time') or '00:00:00'}"
        )
    except (KeyError, TypeError, ValueError):
        return None
    return ordered_at.replace(tzinfo=BILKA_TIME_ZONE)


_FINISHED_STATUSES = frozenset(
    {OrderStatus.PICKED_UP, OrderStatus.NOT_COLLECTED, OrderStatus.CANCELLED, OrderStatus.SOLD_OUT}
)


@dataclass(frozen=True)
class OrderHistory:
    """The customer's latest placed Orders, in the order Bilka lists them."""

    orders: list[Order]

    @property
    def current_order(self) -> Order | None:
        """The unfinished Order with the earliest Pickup Slot, else the latest finished one."""
        unfinished = [order for order in self.orders if not order.finished]
        if unfinished:
            return min(unfinished, key=lambda order: _slot_start(order, _LATEST))
        if not self.orders:
            return None
        return max(self.orders, key=lambda order: _slot_start(order, _EARLIEST))


_EARLIEST = datetime.min.replace(tzinfo=UTC)
_LATEST = datetime.max.replace(tzinfo=UTC)


def _slot_start(order: Order, missing: datetime) -> datetime:
    return order.pickup_slot.starts_at if order.pickup_slot else missing
