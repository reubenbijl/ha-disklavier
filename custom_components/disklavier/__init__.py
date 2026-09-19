"""The Yamaha Disklavier integration."""

from __future__ import annotations

from datetime import timedelta

import voluptuous as vol
from aiodisklavier import Disklavier, DisklavierConnectionError, DisklavierError
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_platform_entity_service
from homeassistant.helpers.typing import ConfigType

from .const import ATTR_DURATION, ATTR_MESSAGE, DOMAIN, HOLD_MAX, SERVICE_HOLD_PLAYBACK
from .coordinator import DisklavierConfigEntry, DisklavierCoordinator

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's action, once, whatever pianos are configured."""
    async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_HOLD_PLAYBACK,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema={
            vol.Required(ATTR_DURATION): vol.All(
                cv.time_period, vol.Range(min=timedelta(seconds=1), max=HOLD_MAX)
            ),
            vol.Optional(ATTR_MESSAGE): vol.All(cv.string, vol.Length(max=100)),
        },
        func="async_hold_playback",
    )
    return True


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
