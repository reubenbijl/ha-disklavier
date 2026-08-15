"""The Yamaha Disklavier integration."""

from __future__ import annotations

from aiodisklavier import Disklavier, DisklavierConnectionError, DisklavierError
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .coordinator import DisklavierConfigEntry, DisklavierCoordinator

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: DisklavierConfigEntry) -> bool:
    """Set up a Disklavier from a config entry."""
    client = Disklavier(entry.data[CONF_HOST], async_get_clientsession(hass))

    try:
        static_info = await client.async_get_static_info()
    except DisklavierConnectionError as err:
        raise ConfigEntryNotReady(f"Could not reach the Disklavier: {err}") from err
    except DisklavierError as err:
        raise ConfigEntryNotReady(
            f"Unexpected response from the Disklavier: {err}"
        ) from err

    coordinator = DisklavierCoordinator(hass, entry, client, static_info)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DisklavierConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
