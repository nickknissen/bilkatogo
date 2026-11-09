"""Event platform for BilkaToGo."""

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BilkaToGoConfigEntry, BilkaToGoData
from .entity import BilkaToGoEntity

PARALLEL_UPDATES = 0

EVENTS: tuple[EventEntityDescription, ...] = (
    EventEntityDescription(
        key="substitution", translation_key="substitution", event_types=["substitution"]
    ),
    EventEntityDescription(
        key="missing_item", translation_key="missing_item", event_types=["missing_item"]
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BilkaToGoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BilkaToGo events."""
    async_add_entities(
        BilkaToGoEvent(entry.runtime_data, description) for description in EVENTS
    )


class BilkaToGoEvent(BilkaToGoEntity, EventEntity):
    """Fire once for each new Substitution or Missing Item."""

    _fired_for: BilkaToGoData | None = None

    async def async_added_to_hass(self) -> None:
        """Fire what the first update announced once the entity is added."""
        await super().async_added_to_hass()
        # Home Assistant drops states written while the entity is being added.
        self.hass.loop.call_soon(self._fire_announced)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._fire_announced()
        super()._handle_coordinator_update()

    @callback
    def _fire_announced(self) -> None:
        """Fire what the latest update announced.

        A failed update keeps the previous data, whose items have already fired.
        """
        data = self.coordinator.data
        if data is self._fired_for:
            return
        self._fired_for = data
        event_type = self.entity_description.key
        for attributes in data.announced.get(event_type, []):
            self._trigger_event(event_type, attributes)
            self.async_write_ha_state()
