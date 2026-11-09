"""Keep the BilkaToGo session in Home Assistant's storage."""

from typing import Any

from bilkatogo import TokenStorage

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1


class HomeAssistantTokenStorage(TokenStorage):
    """Keep the entry's session in a private store, so it survives restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Use the store named after the entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.session", private=True
        )

    async def load(self) -> dict[str, Any] | None:
        """Return the stored session, or None when there is none."""
        return await self._store.async_load()

    async def save(self, data: dict[str, Any]) -> None:
        """Store the session."""
        await self._store.async_save(data)

    async def async_remove(self) -> None:
        """Delete the stored session."""
        await self._store.async_remove()
