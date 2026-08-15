"""Tests for diagnostics output."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.disklavier.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import DISKLAVIER_ID, HOST


async def test_diagnostics_redacts_identifying_data(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Diagnostics get pasted into public issues, so address and serial are redacted."""
    result = await async_get_config_entry_diagnostics(hass, init_integration)

    dumped = str(result)
    assert HOST not in dumped
    assert DISKLAVIER_ID not in dumped


async def test_diagnostics_keeps_what_is_useful(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Redaction must not gut the report: the state that explains a bug stays."""
    result = await async_get_config_entry_diagnostics(hass, init_integration)

    assert result["static_info"]["model"] == "PRO"
    assert result["static_info"]["version"] == "5.24.00"
    assert result["current"]["playback_status"] == "pause"
    assert result["current"]["position_ms"] == 516000
    assert result["master"]["repeat"] == "off"


async def test_diagnostics_without_extended_state(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client
) -> None:
    """A missing master.json shows as null rather than breaking the report."""
    from aiodisklavier import DisklavierResponseError

    mock_client.async_get_master_state.side_effect = DisklavierResponseError("gone")
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    assert result["master"] is None
