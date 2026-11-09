"""Base entity for BilkaToGo."""

from bilkatogo import OrderStatus

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, WEBSITE_URL
from .coordinator import BilkaToGoConfigEntry, BilkaToGoCoordinator

UNRECOGNIZED_STATUS = "unrecognized"


def status_option(status: OrderStatus) -> str:
    """Return the Order Status sensor's option for `status`."""
    return UNRECOGNIZED_STATUS if status is OrderStatus.UNKNOWN else status.value


def account_id(entry: BilkaToGoConfigEntry) -> str:
    """Key the device and entities on the account, so they survive re-adding it.

    The config flow always sets the lower-cased email as the entry's unique ID.
    """
    assert entry.unique_id is not None
    return entry.unique_id


class BilkaToGoEntity(CoordinatorEntity[BilkaToGoCoordinator]):
    """An entity on the account's device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BilkaToGoCoordinator, description: EntityDescription
    ) -> None:
        """Key the entity on the account and the description's key."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{account_id(entry)}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_id(entry))},
            name=entry.title,
            manufacturer="Bilka",
            model="BilkaToGo",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=WEBSITE_URL,
        )
