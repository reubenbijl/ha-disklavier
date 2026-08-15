"""Button entities for the Yamaha Disklavier integration."""

from __future__ import annotations

from aiodisklavier import DisklavierError

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DisklavierConfigEntry, DisklavierCoordinator
from .entity import DisklavierEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DisklavierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Disklavier buttons."""
    async_add_entities([DisklavierTestChordButton(entry.runtime_data)])


class DisklavierTestChordButton(DisklavierEntity, ButtonEntity):
    """Play a C major chord on the piano.

    Useful for confirming the piano really is responding, and for locating which instrument
    an entity belongs to. Unlike the transport commands this goes to the MIDI patch daemon,
    so it will not disturb a loaded or paused song.
    """

    _attr_translation_key = "test_chord"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.static_info.disklavier_id}_test_chord"

    async def async_press(self) -> None:
        """Play the chord."""
        try:
            await self.coordinator.client.async_play_test_chord()
        except DisklavierError as err:
            raise HomeAssistantError(f"Could not play the test chord: {err}") from err
