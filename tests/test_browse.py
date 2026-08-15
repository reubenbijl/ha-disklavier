"""Tests for the media browser and the coordinator's failure paths."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiodisklavier import (
    DisklavierConnectionError,
    DisklavierResponseError,
    Playlist,
    RadioChannel,
    Song,
)
from homeassistant.components.media_player import BrowseMedia
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTITY = "media_player.disklavier_pro"


def _entity(hass: HomeAssistant) -> Any:
    """Reach the media player entity object, for the browse API."""
    return hass.data["entity_components"]["media_player"].get_entity(ENTITY)


async def _browse(hass: HomeAssistant, content_id: str | None = None) -> BrowseMedia:
    """Browse a node."""
    return await _entity(hass).async_browse_media(media_content_id=content_id)


# ----------------------------------------------------------------------
# Browsing each kind of node
# ----------------------------------------------------------------------


async def test_browse_playlists(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """The playlist library lists playlists, which are both playable and expandable."""
    mock_client.async_get_playlists.return_value = [
        Playlist(playlist_id=1, title="RR Christmas")
    ]

    node = await _browse(hass, "playlists/playlists")
    assert [child.title for child in node.children] == ["RR Christmas"]
    assert node.children[0].can_play is True
    assert node.children[0].can_expand is True


async def test_browse_inside_a_playlist(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Expanding a playlist lists its items."""
    mock_client.async_get_playlist_items.return_value = [
        Song(song_id=24, title="Silent Night")
    ]

    node = await _browse(hass, "playlist/playlists/1")
    assert [child.title for child in node.children] == ["Silent Night"]
    mock_client.async_get_playlist_items.assert_awaited()


async def test_browse_radio(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Radio channels are listed and playable."""
    mock_client.async_get_radio_channels.return_value = [
        RadioChannel(channel_id=1, title="Complimentary Channel Sampler")
    ]

    node = await _browse(hass, "radio")
    assert [child.title for child in node.children] == ["Complimentary Channel Sampler"]
    assert node.children[0].can_play is True


@pytest.mark.parametrize(
    ("content_id", "method"),
    [
        ("library/built_in_songs", "async_get_songs"),
        ("playlists/playlists", "async_get_playlists"),
        ("playlist/playlists/1", "async_get_playlist_items"),
        ("radio", "async_get_radio_channels"),
    ],
)
async def test_browse_reports_a_failing_library(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    content_id: str,
    method: str,
) -> None:
    """A library that will not list is an error, not an empty shelf.

    aiodisklavier already returns an empty list for a genuinely empty library, so anything
    raising here is a real fault. Showing it as empty would be indistinguishable from a
    library with nothing in it.
    """
    getattr(mock_client, method).side_effect = DisklavierResponseError("nope")

    with pytest.raises(HomeAssistantError) as err:
        await _browse(hass, content_id)
    assert err.value.translation_key == "browse_failed"


async def test_browse_rejects_an_unknown_group(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A well-formed id naming a library that does not exist is still an error."""
    with pytest.raises(HomeAssistantError) as err:
        await _browse(hass, "library/not_a_library")
    assert err.value.translation_key == "unsupported_media_id"


# ----------------------------------------------------------------------
# Volume stepping
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "method"),
    [("volume_up", "async_volume_up"), ("volume_down", "async_volume_down")],
)
async def test_volume_stepping(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    service: str,
    method: str,
) -> None:
    """Stepping uses the piano's own step, rather than computing a new level."""
    await hass.services.async_call(
        "media_player", service, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )
    getattr(mock_client, method).assert_awaited_once()


# ----------------------------------------------------------------------
# Coordinator failure
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "side_effect",
    [DisklavierConnectionError("unplugged"), DisklavierResponseError("garbage")],
)
async def test_entities_go_unavailable_when_polling_fails(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    side_effect: Exception,
) -> None:
    """A piano that stops answering makes its entities unavailable, not stale.

    Showing the last known state would be worse than showing nothing: a paused piano and
    an unplugged one look identical otherwise.
    """
    assert hass.states.get(ENTITY).state != STATE_UNAVAILABLE

    mock_client.async_get_current_info.side_effect = side_effect
    coordinator = init_integration.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
