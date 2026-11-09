"""Binary sensor platform for BilkaToGo."""

from bilkatogo import OrderStatus

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BilkaToGoConfigEntry
from .entity import BilkaToGoEntity

PARALLEL_UPDATES = 0

READY_FOR_PICKUP = BinarySensorEntityDescription(
    key="ready_for_pickup", translation_key="ready_for_pickup"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BilkaToGoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BilkaToGo binary sensor."""
    async_add_entities([BilkaToGoReadyForPickup(entry.runtime_data, READY_FOR_PICKUP)])


class BilkaToGoReadyForPickup(BilkaToGoEntity, BinarySensorEntity):
    """On while the Current Order is Packed and waiting to be picked up."""

    @property
    def is_on(self) -> bool:
        """Return whether the Current Order is Packed."""
        order = self.coordinator.data.order
        return order is not None and order.status is OrderStatus.PACKED
