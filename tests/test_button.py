"""Tests for the test-chord button."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiodisklavier import DisklavierCommandError
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import DISKLAVIER_ID

ENTITY = "button.disklavier_pro_play_test_chord"


async def test_disabled_by_default(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The button exists but is disabled: pressing it makes a noise.

    That is not something to trigger by accident while looking at the device page.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(ENTITY)

    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert entry.unique_id == f"{DISKLAVIER_ID}_test_chord"
    # Disabled entities have no state.
    assert hass.states.get(ENTITY) is None


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_press_plays_the_chord(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Once enabled, pressing it reaches the client."""
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )
    mock_client.async_play_test_chord.assert_awaited_once()


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_failure_is_translated(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """A rejected press surfaces as a translated error."""
    mock_client.async_play_test_chord.side_effect = DisklavierCommandError("no")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: ENTITY}, blocking=True
        )
    assert err.value.translation_key == "command_failed"
