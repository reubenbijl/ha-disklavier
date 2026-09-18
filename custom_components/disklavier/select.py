"""Select entities for the Yamaha Disklavier integration."""

from __future__ import annotations

from aiodisklavier import QuietMode
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
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

    ``headphone`` is among the options so that the state can say so: the piano reports it
    for as long as headphones are plugged in, and an automation may well want to know --
    to mute an amplifier, say. It is the piano's to set, though, not ours. The firmware
    answers a request for it with HTTP 400, so choosing it is refused here with a reason
    rather than sent.
    """

    _attr_translation_key = "quiet_mode"
    _attr_options = [mode.value for mode in QuietMode]

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.static_info.disklavier_id}_quiet_mode"

    @property
    def current_option(self) -> str | None:
        """Return the current mode.

        ``None`` -- shown as unknown -- when the piano reports a mode aiodisklavier has
        no name for, or none at all, as a model without a silent system may.
        """
        mode = self.coordinator.data.current.quiet_status
        return None if mode is None else mode.value

    async def async_select_option(self, option: str) -> None:
        """Switch between acoustic and quiet."""
        mode = QuietMode(option)
        if mode is QuietMode.HEADPHONE:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="headphone_not_selectable",
            )
        await self._async_call(self.coordinator.client.async_set_quiet_mode(mode))
