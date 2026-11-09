"""The BilkaToGo integration."""

from typing import Any

from bilkatogo import REQUEST_TIMEOUT_SECONDS, BilkaToGoClient

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.helpers.typing import ConfigType

from .announcer import announcers
from .const import DOMAIN, PLATFORMS
from .coordinator import BilkaToGoConfigEntry, BilkaToGoCoordinator
from .entity import account_id
from .services import async_setup_services
from .token_storage import HomeAssistantTokenStorage

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the BilkaToGo actions."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: BilkaToGoConfigEntry) -> bool:
    """Migrate an entry from an older minor version."""
    if entry.minor_version < 2:
        await _async_key_on_the_account(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def _async_key_on_the_account(
    hass: HomeAssistant, entry: BilkaToGoConfigEntry
) -> None:
    """Move the device and entities from the entry ID to the account (1.1 to 1.2)."""
    old_prefix = f"{entry.entry_id}_"

    @callback
    def migrate(entity: er.RegistryEntry) -> dict[str, Any] | None:
        if not entity.unique_id.startswith(old_prefix):
            return None
        key = entity.unique_id.removeprefix(old_prefix)
        return {"new_unique_id": f"{account_id(entry)}_{key}"}

    await er.async_migrate_entries(hass, entry.entry_id, migrate)
    devices = dr.async_get(hass)
    if device := devices.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    ):
        devices.async_update_device(
            device.id, new_identifiers={(DOMAIN, account_id(entry))}
        )


async def async_setup_entry(hass: HomeAssistant, entry: BilkaToGoConfigEntry) -> bool:
    """Set up BilkaToGo from a config entry."""
    client = BilkaToGoClient(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        token_storage=HomeAssistantTokenStorage(hass, entry.entry_id),
        httpx_client=create_async_httpx_client(hass, timeout=REQUEST_TIMEOUT_SECONDS),
    )
    coordinator = BilkaToGoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BilkaToGoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BilkaToGoConfigEntry) -> None:
    """Delete the entry's stored session and announced items."""
    await HomeAssistantTokenStorage(hass, entry.entry_id).async_remove()
    for announcer in announcers(hass, entry.entry_id):
        await announcer.async_remove()
