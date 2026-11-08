from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from bilkatogo import Order, OrderStatus, SlotAvailability

from .announcer import missing_item_data, substitution_data
from .const import CURRENCY
from .coordinator import BilkaToGoConfigEntry, BilkaToGoData
from .entity import BilkaToGoEntity, status_option

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BilkaToGoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[BilkaToGoData], StateType | datetime | Decimal]
    attributes_fn: Callable[[BilkaToGoData], Mapping[str, Any] | None] = lambda _: None
    available_fn: Callable[[BilkaToGoData], bool] = lambda _: True


def _slot_time(order: Order | None, *, start: bool) -> datetime | None:
    slot = order.pickup_slot if order else None
    if slot is None:
        return None
    return slot.starts_at if start else slot.ends_at


def _status_attributes(data: BilkaToGoData) -> dict[str, Any] | None:
    order = data.order
    if order is None:
        return None
    return {"status_code": order.status_code, "order_number": order.order_number}


def _slot_attributes(data: BilkaToGoData) -> dict[str, Any] | None:
    if data.order is None:
        return None
    end = _slot_time(data.order, start=False)
    return {"pickup_slot_end": end.isoformat() if end else None}


def _order_total(data: BilkaToGoData) -> Decimal | None:
    if data.details:
        return data.details.order_total
    if data.order:
        return data.order.order_total
    return None


def _order_total_attributes(data: BilkaToGoData) -> dict[str, Any] | None:
    order, details, total = data.order, data.details, _order_total(data)
    if order is None or total is None:
        return None
    attributes = {
        "ordered_total": float(order.ordered_total),
        "difference": float(total - order.ordered_total),
    }
    if details:
        attributes |= {
            "deposit": float(details.deposit),
            "service_fee": float(details.service_fee),
            "packaging": float(details.packaging),
            "delivery_fee": float(details.delivery_fee),
            "discount": float(details.discount),
        }
    return attributes


def _next_pickup_slot(data: BilkaToGoData) -> datetime | None:
    next_open = data.pickup_slots.next_open if data.pickup_slots else None
    return next_open.slot.starts_at if next_open else None


def _next_pickup_slot_attributes(data: BilkaToGoData) -> dict[str, Any] | None:
    slots = data.pickup_slots
    if slots is None:
        return None
    attributes: dict[str, Any] = {
        "open_slots": sum(slot.availability is SlotAvailability.OPEN for slot in slots.slots)
    }
    if slots.pickup_point:
        attributes["pickup_point"] = slots.pickup_point.name
    if next_open := slots.next_open:
        attributes |= {
            "end": next_open.slot.ends_at.isoformat(),
            "availability": next_open.availability.value,
            "order_before": next_open.order_before,
            "note": next_open.note,
        }
    return attributes


SENSORS: tuple[BilkaToGoSensorDescription, ...] = (
    BilkaToGoSensorDescription(
        key="order_status",
        translation_key="order_status",
        device_class=SensorDeviceClass.ENUM,
        options=[status_option(status) for status in OrderStatus],
        value_fn=lambda data: status_option(data.order.status) if data.order else None,
        attributes_fn=_status_attributes,
    ),
    BilkaToGoSensorDescription(
        key="order_total",
        translation_key="order_total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=_order_total,
        attributes_fn=_order_total_attributes,
    ),
    BilkaToGoSensorDescription(
        key="pickup_slot",
        translation_key="pickup_slot",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _slot_time(data.order, start=True),
        attributes_fn=_slot_attributes,
    ),
    BilkaToGoSensorDescription(
        key="pickup_point",
        translation_key="pickup_point",
        value_fn=lambda data: (
            data.order.pickup_point.name if data.order and data.order.pickup_point else None
        ),
    ),
    BilkaToGoSensorDescription(
        key="pickup_code",
        translation_key="pickup_code",
        value_fn=lambda data: (
            data.details.pickup_code.code if data.details and data.details.pickup_code else None
        ),
    ),
    BilkaToGoSensorDescription(
        key="substitutions",
        translation_key="substitutions",
        value_fn=lambda data: len(data.details.substitutions) if data.details else None,
        attributes_fn=lambda data: (
            {
                "substitutions": [
                    substitution_data(substitution) for substitution in data.details.substitutions
                ]
            }
            if data.details
            else None
        ),
    ),
    BilkaToGoSensorDescription(
        key="missing_items",
        translation_key="missing_items",
        value_fn=lambda data: len(data.details.missing_items) if data.details else None,
        attributes_fn=lambda data: (
            {"missing_items": [missing_item_data(item) for item in data.details.missing_items]}
            if data.details
            else None
        ),
    ),
    BilkaToGoSensorDescription(
        key="next_pickup_slot",
        translation_key="next_pickup_slot",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_pickup_slot,
        attributes_fn=_next_pickup_slot_attributes,
        available_fn=lambda data: data.pickup_slots is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BilkaToGoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(BilkaToGoSensor(entry.runtime_data, description) for description in SENSORS)


class BilkaToGoSensor(BilkaToGoEntity, SensorEntity):
    entity_description: BilkaToGoSensorDescription

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self.coordinator.data)

    @property
    def native_value(self) -> StateType | datetime | Decimal:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        return self.entity_description.attributes_fn(self.coordinator.data)
