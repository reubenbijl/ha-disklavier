"""Base entity for the Yamaha Disklavier integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DisklavierCoordinator


class DisklavierEntity(CoordinatorEntity[DisklavierCoordinator]):
    """Common device wiring for every Disklavier entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        static_info = coordinator.static_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, static_info.disklavier_id)},
            manufacturer="Yamaha",
            model=f"Disklavier ENSPIRE {static_info.model}",
            name=f"Disklavier {static_info.model}",
            sw_version=static_info.version,
            serial_number=static_info.disklavier_id,
            configuration_url=f"http://{coordinator.client.host}/ctrl/",
        )

    @property
    def available(self) -> bool:
        """Whether the piano is responding."""
        return super().available
