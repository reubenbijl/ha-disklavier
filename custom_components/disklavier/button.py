"""Button entities for the Yamaha Disklavier integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DisklavierConfigEntry, DisklavierCoordinator
from .entity import DisklavierEntity

# Commands are sent one at a time; the piano is a single small web server.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DisklavierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Disklavier buttons."""
    async_add_entities([DisklavierTestChordButton(entry.runtime_data)])


class DisklavierTestChordButton(DisklavierEntity, ButtonEntity):
    """Play a chord on the piano.

    Useful for confirming the piano really is responding, and for working out which
    instrument an entity belongs to. Unlike the transport controls this goes to the MIDI
    patch daemon, so it will not disturb a loaded or paused song.

    Disabled by default: it makes a noise, which is not something to fire accidentally
    while browsing the device page.
    """

    _attr_translation_key = "test_chord"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.static_info.disklavier_id}_test_chord"

    async def async_press(self) -> None:
        """Play the chord."""
        await self._async_call(self.coordinator.client.async_play_test_chord())
