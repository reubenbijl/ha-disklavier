"""Base entity for the Yamaha Disklavier integration."""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from aiodisklavier import DisklavierError
from homeassistant.core import CALLBACK_TYPE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import COMMAND_SETTLE_SECONDS, DOMAIN, MANUFACTURER
from .coordinator import DisklavierCoordinator


class DisklavierEntity(CoordinatorEntity[DisklavierCoordinator]):
    """Common device wiring and error handling for every Disklavier entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._cancel_settle_refresh: CALLBACK_TYPE | None = None
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
        """Send a command, then refresh once the firmware has caught up.

        Library errors become a ``HomeAssistantError`` carrying a translation key, so what
        the user sees is localised rather than raw client output.

        The refresh is not immediate: the firmware keeps reporting the state a command
        just replaced for a moment, so polling straight away would only confirm stale
        state — and ``async_request_refresh`` is debounced besides, which can defer a
        burst of commands to well past the next scheduled poll. Instead the settle is
        waited out and a real refresh forced, keeping the UI a beat behind the command
        rather than a poll interval behind. Only the newest command's refresh is kept.
        """
        try:
            await coro
        except DisklavierError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if self._cancel_settle_refresh is not None:
            self._cancel_settle_refresh()
        self._cancel_settle_refresh = async_call_later(
            self.hass, COMMAND_SETTLE_SECONDS, self._async_settle_refresh
        )

    async def _async_settle_refresh(self, _now: datetime) -> None:
        """Force a coordinator refresh, once the firmware's settle has passed."""
        self._cancel_settle_refresh = None
        await self.coordinator.async_refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Drop any pending post-command refresh."""
        if self._cancel_settle_refresh is not None:
            self._cancel_settle_refresh()
            self._cancel_settle_refresh = None
        await super().async_will_remove_from_hass()
