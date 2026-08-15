"""Base entity for the Yamaha Disklavier integration."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from aiodisklavier import DisklavierError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DisklavierCoordinator


class DisklavierEntity(CoordinatorEntity[DisklavierCoordinator]):
    """Common device wiring and error handling for every Disklavier entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        static_info = coordinator.static_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, static_info.disklavier_id)},
            manufacturer=MANUFACTURER,
            model=f"Disklavier ENSPIRE {static_info.model}",
            name=f"Disklavier {static_info.model}",
            sw_version=static_info.version,
            serial_number=static_info.disklavier_id,
            configuration_url=f"http://{coordinator.client.host}/ctrl/",
        )

    async def _async_call(self, coro: Coroutine[Any, Any, None]) -> None:
        """Send a command, then refresh so the UI reflects it promptly.

        Library errors become a ``HomeAssistantError`` carrying a translation key, so what
        the user sees is localised rather than raw client output.
        """
        try:
            await coro
        except DisklavierError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()
