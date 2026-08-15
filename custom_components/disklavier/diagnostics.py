"""Diagnostics support for the Yamaha Disklavier integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .coordinator import DisklavierConfigEntry

TO_REDACT = {CONF_HOST, "disklavier_id", "serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DisklavierConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "static_info": async_redact_data(asdict(coordinator.static_info), TO_REDACT),
        "current": asdict(data.current),
        "master": asdict(data.master) if data.master else None,
    }
