import re
from dataclasses import dataclass, field
from datetime import date, time
from enum import StrEnum
from typing import Any

from .base import BilkaDataClass
from .cart import Cart
from .order import PickupPoint, PickupSlot

# Bilka's `availability_bit`, seen on a day and on an interval: 1 open, 2 closed because its
# order deadline passed, 4 sold out. A day with open and sold-out intervals has 5 (1 | 4) and a
# day without intervals 0. Seen in two captures only (ADR 0006).
OPEN_BIT = 1
SOLD_OUT_BIT = 4
SOLD_OUT_TEXT = "udsolgt"


class SlotAvailability(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    """Past its order deadline, or closed by Bilka for another reason."""
    SOLD_OUT = "sold_out"


@dataclass
class AvailablePickupSlot(BilkaDataClass):
    slot: PickupSlot
    availability: SlotAvailability
    order_before: str | None
    """Bilka's deadline for ordering, as the website words it, e.g. "på søndag kl. 15:00"."""
    note: str | None
    """Bilka's text for the slot, e.g. when a night slot is delivered, or else why it is
    closed, e.g. "udsolgt"."""
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_interval(
        cls, day: date, day_data: dict[str, Any], interval: dict[str, Any]
    ) -> AvailablePickupSlot | None:
        try:
            slot = PickupSlot(
                date=day,
                start=time.fromisoformat(interval["intervalStart"]),
                end=time.fromisoformat(interval["intervalEnd"]),
            )
        except KeyError, TypeError, ValueError:
            return None
        return cls(
            _raw=interval,
            slot=slot,
            availability=_availability(day_data, interval),
            order_before=interval.get("orderBeforeTime") or None,
            note=_note(interval),
        )


def _note(interval: dict[str, Any]) -> str | None:
    messages = [
        message.get("message") or "" if isinstance(message, dict) else str(message)
        for message in interval.get("info") or []
    ]
    text = " ".join(re.sub(r"<[^>]+>", "", " ".join(messages)).split())
    return text or interval.get("closed_text") or None


def _availability(day: dict[str, Any], interval: dict[str, Any]) -> SlotAvailability:
    bit = interval.get("availability_bit")
    if not interval.get("closed") and isinstance(bit, int) and bit & OPEN_BIT:
        return SlotAvailability.OPEN
    if any(
        part.get("availability_bit") == SOLD_OUT_BIT or part.get("closed_text") == SOLD_OUT_TEXT
        for part in (day, interval)
    ):
        return SlotAvailability.SOLD_OUT
    return SlotAvailability.CLOSED


@dataclass
class PickupSlots(BilkaDataClass):
    """The Pickup Slots of the coming days for what the Cart has: its Pickup Point or Home
    Delivery. Bilka's `AvailableDeliveryDates`."""

    slots: list[AvailablePickupSlot]
    days_without_slots: dict[date, str]
    """Days in the window Bilka offers no Pickup Slot on, with its reason, e.g. "udsolgt";
    empty when it gives none, as for a day the Pickup Point is closed."""
    chosen: PickupSlot | None
    """The Pickup Slot chosen in the Cart, while it lies ahead."""
    pickup_point: PickupPoint | None
    """The Cart's Pickup Point; None for Home Delivery or when the Cart has none."""
    home_delivery: bool

    @property
    def open_slots(self) -> list[AvailablePickupSlot]:
        """The Pickup Slots that can still be chosen."""
        return [slot for slot in self.slots if slot.availability is SlotAvailability.OPEN]

    def starting_at(self, day: date, start: time) -> list[AvailablePickupSlot]:
        """The Pickup Slots on `day` that start at `start`, of any availability. Every capture
        has at most one."""
        return [slot for slot in self.slots if (slot.slot.date, slot.slot.start) == (day, start)]

    @property
    def next_open(self) -> AvailablePickupSlot | None:
        """The earliest Pickup Slot that can still be chosen."""
        open_slots = self.open_slots
        if not open_slots:
            return None
        return min(open_slots, key=lambda slot: slot.slot.starts_at)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], cart: Cart, *, first_day: date, last_day: date
    ) -> PickupSlots:
        """`data` is Bilka's answer for the `cart`; only days from `first_day` to `last_day`,
        both included, are kept."""
        slots = []
        days_without_slots = {}
        for day_data in _dicts(data.get("availableIntervals")):
            day = _date(day_data.get("date"))
            if day is None or not first_day <= day <= last_day:
                continue
            day_slots = [
                slot
                for interval in _dicts(day_data.get("intervals"))
                if (slot := AvailablePickupSlot.from_interval(day, day_data, interval))
            ]
            if not day_slots:
                days_without_slots[day] = day_data.get("closed_text") or ""
            slots.extend(day_slots)
        return cls(
            slots=slots,
            days_without_slots=days_without_slots,
            # The Cart's, not the copy at the top of Bilka's answer: one source for the term.
            chosen=cart.pickup_slot,
            pickup_point=cart.pickup_point,
            home_delivery=cart.home_delivery,
        )


@dataclass
class ChosenPickupSlot(BilkaDataClass):
    """The Cart's Pickup Slot after choosing one. Choosing holds nothing: the slot can still
    sell out until the Order is placed."""

    chosen: PickupSlot
    replaced: PickupSlot | None
    """The slot the Cart had before, while it lay ahead; None when it had none. The same as
    `chosen` when that was chosen already."""
    changed: bool
    """False when the slot was chosen already and nothing was sent to Bilka."""


def _dicts(value: Any) -> list[dict[str, Any]]:
    """The objects in a list from Bilka, skipping anything else it might send."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value)
    except TypeError, ValueError:
        return None
