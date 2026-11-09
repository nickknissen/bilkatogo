"""Calendar platform for BilkaToGo."""

from datetime import datetime

from bilkatogo import Order, OrderStatus, PickupSlot

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityDescription,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import BilkaToGoConfigEntry
from .entity import BilkaToGoEntity

PARALLEL_UPDATES = 0

PICKUP_SLOTS = CalendarEntityDescription(
    key="pickup_slots", translation_key="pickup_slots"
)

LEFT_OUT_STATUSES = frozenset({OrderStatus.CANCELLED, OrderStatus.SOLD_OUT})

EVENT_SUMMARY = "BilkaToGo"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BilkaToGoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BilkaToGo calendar."""
    async_add_entities([BilkaToGoCalendar(entry.runtime_data, PICKUP_SLOTS)])


class BilkaToGoCalendar(BilkaToGoEntity, CalendarEntity):
    """The Pickup Slots of the Orders in the order history."""

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next Pickup Slot."""
        now = dt_util.now()
        return next((event for event in self._events() if event.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the Pickup Slots that overlap the range."""
        return [
            event
            for event in self._events()
            if event.end > start_date and event.start < end_date
        ]

    def _events(self) -> list[CalendarEvent]:
        events = [
            self._event(order, order.pickup_slot)
            for order in self.coordinator.data.orders
            if order.pickup_slot is not None and order.status not in LEFT_OUT_STATUSES
        ]
        return sorted(events, key=lambda event: event.start_datetime_local)

    def _event(self, order: Order, slot: PickupSlot) -> CalendarEvent:
        """Describe an Order's Pickup Slot with Bilka's own data, which needs no translation.

        Home Delivery shows as an event without a location; its address is personal.
        """
        return CalendarEvent(
            start=slot.starts_at,
            end=slot.ends_at,
            summary=EVENT_SUMMARY,
            description=f"#{order.order_number}",
            location=None if order.home_delivery else self._pickup_point_name(order),
            uid=order.order_number,
        )

    @staticmethod
    def _pickup_point_name(order: Order) -> str | None:
        return order.pickup_point.name if order.pickup_point else None
