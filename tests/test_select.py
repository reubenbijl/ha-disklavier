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
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import setup_integration

ENTITY = "select.disklavier_pro_quiet_mode"


async def test_reports_current_mode(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The select mirrors the piano's quiet status."""
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "acoustic"
    assert state.attributes["options"] == ["acoustic", "quiet", "headphone"]


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
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY).state == "quiet"


async def test_reports_headphones(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """Plugging headphones in is a third mode, and the state says so.

    Found on hardware: ``quiet_status`` reads ``headphone`` for as long as they are in.
    aiodisklavier used to read that as acoustic, so this entity did too.
    """
    mock_client.async_get_current_info.return_value = replace(
        current_info, quiet_status=QuietMode.HEADPHONE
    )
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY).state == "headphone"


async def test_an_unrecognised_mode_is_unknown(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """A mode aiodisklavier has no name for arrives as None, and shows as unknown.

    Not as acoustic: a confident answer to a question nobody could answer is how the
    headphone mode went unnoticed.
    """
    mock_client.async_get_current_info.return_value = replace(
        current_info, quiet_status=None
    )
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY).state == STATE_UNKNOWN


async def test_headphone_mode_cannot_be_chosen(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Headphone mode is the piano's to set, so choosing it is refused with a reason.

    The firmware answers a request for it with HTTP 400. It is among the options only so
    that the state can report it.
    """
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: ENTITY, ATTR_OPTION: "headphone"},
            blocking=True,
        )
    assert err.value.translation_key == "headphone_not_selectable"
    mock_client.async_set_quiet_mode.assert_not_awaited()


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
