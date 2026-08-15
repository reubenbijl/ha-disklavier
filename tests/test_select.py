"""Tests for the quiet-mode select."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiodisklavier import CurrentInfo, DisklavierCommandError, QuietMode
from homeassistant.components.select import (
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTITY = "select.disklavier_pro_quiet_mode"


async def test_reports_current_mode(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The select mirrors the piano's quiet status."""
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "acoustic"
    assert state.attributes["options"] == ["acoustic", "quiet"]


async def test_reports_quiet(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """Quiet mode is reflected too."""
    mock_client.async_get_current_info.return_value = replace(
        current_info, quiet_status=QuietMode.QUIET
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "quiet"


@pytest.mark.parametrize(
    ("option", "expected"),
    [("quiet", QuietMode.QUIET), ("acoustic", QuietMode.ACOUSTIC)],
)
async def test_select_option(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    option: str,
    expected: QuietMode,
) -> None:
    """Choosing a mode sends it to the piano."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: ENTITY, ATTR_OPTION: option},
        blocking=True,
    )
    mock_client.async_set_quiet_mode.assert_awaited_once_with(expected)


async def test_failure_is_translated(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A rejected change surfaces as a translated error."""
    mock_client.async_set_quiet_mode.side_effect = DisklavierCommandError("no")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: ENTITY, ATTR_OPTION: "quiet"},
            blocking=True,
        )
    assert err.value.translation_key == "command_failed"
