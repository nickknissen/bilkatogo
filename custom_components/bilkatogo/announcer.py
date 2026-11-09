"""Remember which Substitutions and Missing Items have been announced."""

from collections.abc import Callable, Sequence
from typing import Any

from bilkatogo import MissingItem, OrderDetails, Substitution

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1


def substitution_data(substitution: Substitution) -> dict[str, Any]:
    """Describe a Substitution as event and attribute data."""
    return {
        "original_product": substitution.original_product,
        "original_article_number": substitution.original_article_number,
        "original_size": substitution.original_size,
        "ordered_quantity": substitution.ordered_quantity,
        "replacement_product": substitution.replacement_product,
        "replacement_article_number": substitution.replacement_article_number,
        "replacement_size": substitution.replacement_size,
        "quantity": substitution.quantity,
        "replacement_price": float(substitution.replacement_price),
        "replacement_line_total": float(substitution.replacement_line_total),
    }


def missing_item_data(item: MissingItem) -> dict[str, Any]:
    """Describe a Missing Item as event and attribute data."""
    return {
        "product": item.product,
        "article_number": item.article_number,
        "ordered_quantity": item.ordered_quantity,
        "missing_quantity": item.missing_quantity,
    }


class Announcer[T]:
    """Announce each item of an Order once, remembering what was announced across restarts.

    Each new item is returned, so the event entity of the same event type can fire it.

    Items stored under `legacy_key_field` instead of `key_field` were announced by an older
    version and are not announced again. They are rewritten under `key_field` the next time
    their Order's items are announced.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        storage_suffix: str,
        event_type: str,
        key_field: str,
        legacy_key_field: str | None = None,
        items: Callable[[OrderDetails], Sequence[T]],
        data: Callable[[T], dict[str, Any]],
    ) -> None:
        """Keep the announced items in the entry's store named by `storage_suffix`."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.{storage_suffix}"
        )
        self.event_type = event_type
        self._key_field = key_field
        self._legacy_key_field = legacy_key_field
        self._items = items
        self._data = data
        self._announced: set[tuple[str, str]] = set()
        self._legacy: set[tuple[str, str]] = set()

    async def async_load(self) -> None:
        """Load what was announced before the last restart."""
        data = await self._store.async_load() or {}
        for item in data.get("announced", []):
            if self._key_field in item:
                self._announced.add((item["order_number"], item[self._key_field]))
            elif self._legacy_key_field in item:
                self._legacy.add((item["order_number"], item[self._legacy_key_field]))

    async def async_announce(self, details: OrderDetails) -> list[dict[str, Any]]:
        """Return the Order's items not announced before, and remember them."""
        order_number = details.order_number
        legacy = {key for number, key in self._legacy if number == order_number}
        self._legacy -= {(order_number, key) for key in legacy}
        changed = bool(legacy)
        new = []
        for item in self._items(details):
            key = (order_number, getattr(item, self._key_field))
            if key in self._announced:
                continue
            self._announced.add(key)
            changed = True
            if (
                self._legacy_key_field is None
                or getattr(item, self._legacy_key_field) not in legacy
            ):
                new.append(item)
        if changed:
            await self._async_save()
        return [{"order_number": order_number, **self._data(item)} for item in new]

    async def _async_save(self) -> None:
        announced = [
            {"order_number": order_number, self._key_field: key}
            for order_number, key in sorted(self._announced)
        ]
        if self._legacy_key_field is not None:
            announced += [
                {"order_number": order_number, self._legacy_key_field: key}
                for order_number, key in sorted(self._legacy)
            ]
        await self._store.async_save({"announced": announced})

    async def async_remove(self) -> None:
        """Delete the store."""
        await self._store.async_remove()


def announcers(hass: HomeAssistant, entry_id: str) -> tuple[Announcer[Any], ...]:
    """Return the entry's announcers: one for Substitutions, one for Missing Items."""
    return (
        Announcer(
            hass,
            entry_id,
            storage_suffix="substitutions",
            event_type="substitution",
            key_field="original_article_number",
            # Up to 0.5.0, Substitutions were keyed on the Original Product's name.
            legacy_key_field="original_product",
            items=lambda details: details.substitutions,
            data=substitution_data,
        ),
        Announcer(
            hass,
            entry_id,
            storage_suffix="missing_items",
            event_type="missing_item",
            key_field="article_number",
            items=lambda details: details.missing_items,
            data=missing_item_data,
        ),
    )
