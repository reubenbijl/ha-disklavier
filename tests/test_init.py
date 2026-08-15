"""Tests for setting up and tearing down the Disklavier integration."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiodisklavier import DisklavierConnectionError, DisklavierResponseError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.disklavier.const import DOMAIN

from .conftest import DISKLAVIER_ID


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads, then unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    "side_effect",
    [DisklavierConnectionError("unreachable"), DisklavierResponseError("nonsense")],
)
async def test_setup_retries_when_the_piano_is_not_answering(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    side_effect: Exception,
) -> None:
    """A piano that cannot be reached leaves the entry retrying, not failed.

    Pianos get switched off at the wall, so this is an ordinary condition rather than a
    misconfiguration.
    """
    mock_client.async_get_static_info.side_effect = side_effect
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_registry_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The piano appears as one device, identified by its own id."""
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, DISKLAVIER_ID)})

    assert device is not None
    assert device.manufacturer == "Yamaha"
    assert device.model == "Disklavier ENSPIRE PRO"
    assert device.sw_version == "5.24.00"
    assert device.serial_number == DISKLAVIER_ID
    assert device.configuration_url is not None


async def test_extended_state_is_best_effort(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Losing master.json must not take the whole integration down.

    Only repeat and shuffle come from that endpoint, so everything else keeps working and
    those two report unknown.
    """
    mock_client.async_get_master_state.side_effect = DisklavierResponseError("gone")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    state = hass.states.get("media_player.disklavier_pro")
    assert state is not None
    assert state.attributes.get("repeat") is None
    assert state.attributes.get("shuffle") is None
