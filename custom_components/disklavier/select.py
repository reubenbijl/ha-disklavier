"""Select entities for the Yamaha Disklavier integration."""

from __future__ import annotations

from aiodisklavier import QuietMode
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DisklavierConfigEntry, DisklavierCoordinator
from .entity import DisklavierEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DisklavierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Disklavier select entities."""
    async_add_entities([DisklavierQuietModeSelect(entry.runtime_data)])


class DisklavierQuietModeSelect(DisklavierEntity, SelectEntity):
    """Whether the hammers physically strike the strings.

    This is the control that makes a Disklavier a Disklavier: in quiet mode the keys still
    move but the hammers are stopped short, so playback is silent in the room and audible
    only through the speakers or headphones.
    """

    _attr_translation_key = "quiet_mode"
    _attr_options = [mode.value for mode in QuietMode]

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.static_info.disklavier_id}_quiet_mode"

    @property
    def current_option(self) -> str:
        """Return the current mode."""
        return self.coordinator.data.current.quiet_status.value

    async def async_select_option(self, option: str) -> None:
        """Switch between acoustic and quiet."""
        await self._async_call(
            self.coordinator.client.async_set_quiet_mode(QuietMode(option))
        )
