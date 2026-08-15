"""Tests for the Disklavier media player."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiodisklavier import (
    CurrentInfo,
    DisklavierCommandError,
    PlaybackStatus,
    PlaylistGroup,
    PowerStatus,
    RepeatMode,
    SongGroup,
)
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_REPEAT,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_SHUFFLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.components.media_player import (
    DOMAIN as MP_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_SEEK,
    SERVICE_MEDIA_STOP,
    SERVICE_REPEAT_SET,
    SERVICE_SHUFFLE_SET,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_SET,
    STATE_IDLE,
    STATE_OFF,
    STATE_PAUSED,
    STATE_PLAYING,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTITY = "media_player.disklavier_pro"


# ----------------------------------------------------------------------
# State
# ----------------------------------------------------------------------


async def test_state_and_attributes(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A paused song reports its metadata, position and volume."""
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == STATE_PAUSED
    assert state.attributes["media_title"] == "Beethoven - Symphony No. 7, Movement 1."
    assert state.attributes["media_artist"] == "Ludwig van Beethoven"
    assert state.attributes["media_duration"] == 851
    assert state.attributes["media_position"] == 516
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 1.0
    assert state.attributes["media_position_updated_at"] is not None


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"playback_status": PlaybackStatus.PLAY}, STATE_PLAYING),
        ({"playback_status": PlaybackStatus.PAUSE}, STATE_PAUSED),
        # The piano has no stop state: stop reports as paused at position zero.
        ({"playback_status": PlaybackStatus.PAUSE, "position_ms": 0}, STATE_IDLE),
        # Standby, and the transitional wake-up that follows it, are both "off" -- the
        # piano ignores commands for about twelve seconds while waking.
        ({"power_status": PowerStatus.SLEEP}, STATE_OFF),
        ({"power_status": PowerStatus.WAKEUP}, STATE_OFF),
    ],
)
async def test_state_mapping(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    changes: dict,
    expected: str,
) -> None:
    """Each piano state maps onto the right Home Assistant state."""
    mock_client.async_get_current_info.return_value = replace(current_info, **changes)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == expected


@pytest.mark.parametrize(
    ("repeat", "ha_repeat", "shuffle"),
    [
        (RepeatMode.OFF, "off", False),
        (RepeatMode.ONE, "one", False),
        (RepeatMode.MEDIA_ALL, "all", False),
        (RepeatMode.ALBUM_ALL, "all", False),
        (RepeatMode.MEDIA_SHUFFLE, "all", True),
        (RepeatMode.PLAYLIST_SHUFFLE, "all", True),
    ],
)
async def test_repeat_and_shuffle_are_unfolded(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    master_state,
    repeat: RepeatMode,
    ha_repeat: str,
    shuffle: bool,
) -> None:
    """The piano's single combined setting becomes two separate controls."""
    mock_client.async_get_master_state.return_value = replace(
        master_state, repeat=repeat
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.attributes[ATTR_MEDIA_REPEAT] == ha_repeat
    assert state.attributes[ATTR_MEDIA_SHUFFLE] is shuffle


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "method"),
    [
        (SERVICE_MEDIA_PLAY, "async_play"),
        (SERVICE_MEDIA_PAUSE, "async_pause"),
        (SERVICE_MEDIA_STOP, "async_stop"),
        (SERVICE_MEDIA_NEXT_TRACK, "async_next_song"),
        (SERVICE_MEDIA_PREVIOUS_TRACK, "async_previous_song"),
        (SERVICE_TURN_ON, "async_turn_on"),
        (SERVICE_TURN_OFF, "async_turn_off"),
    ],
)
async def test_transport_services(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    service: str,
    method: str,
) -> None:
    """Each transport service reaches the matching client call."""
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )
    getattr(mock_client, method).assert_awaited_once()


async def test_volume_is_scaled_to_the_piano(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Home Assistant's 0..1 becomes the piano's 0..100."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_VOLUME_SET,
        {ATTR_ENTITY_ID: ENTITY, ATTR_MEDIA_VOLUME_LEVEL: 0.42},
        blocking=True,
    )
    mock_client.async_set_volume.assert_awaited_once_with(42)


async def test_seek_is_converted_to_milliseconds(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Seeking uses milliseconds, which the open API cannot do at all."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_MEDIA_SEEK,
        {ATTR_ENTITY_ID: ENTITY, ATTR_MEDIA_SEEK_POSITION: 42},
        blocking=True,
    )
    mock_client.async_seek.assert_awaited_once_with(42000)


@pytest.mark.parametrize(
    ("repeat", "expected"),
    [("off", RepeatMode.OFF), ("one", RepeatMode.ONE), ("all", RepeatMode.MEDIA_ALL)],
)
async def test_set_repeat(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    repeat: str,
    expected: RepeatMode,
) -> None:
    """Repeat folds back into the piano's combined setting."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_REPEAT_SET,
        {ATTR_ENTITY_ID: ENTITY, ATTR_MEDIA_REPEAT: repeat},
        blocking=True,
    )
    mock_client.async_set_repeat.assert_awaited_once_with(expected)


async def test_shuffle_wins_over_repeat(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """The piano cannot shuffle without repeating, so shuffle takes precedence."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_SHUFFLE_SET,
        {ATTR_ENTITY_ID: ENTITY, ATTR_MEDIA_SHUFFLE: True},
        blocking=True,
    )
    mock_client.async_set_repeat.assert_awaited_once_with(RepeatMode.MEDIA_SHUFFLE)


async def test_command_failure_is_translated(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A rejected command surfaces as a translated Home Assistant error."""
    mock_client.async_play.side_effect = DisklavierCommandError("rejected")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_MEDIA_PLAY, {ATTR_ENTITY_ID: ENTITY}, blocking=True
        )
    assert err.value.translation_key == "command_failed"


# ----------------------------------------------------------------------
# Playing media
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content_id", "method", "args"),
    [
        ("song/built_in_songs/1", "async_play_song", (1, SongGroup.BUILT_IN_SONGS)),
        ("album/built_in_songs/3", "async_play_album", (3, SongGroup.BUILT_IN_SONGS)),
        ("playlist/playlists/1", "async_play_playlist", (1, PlaylistGroup.PLAYLISTS)),
        (
            "playlist_item/playlists/7",
            "async_play_playlist_item",
            (7, PlaylistGroup.PLAYLISTS),
        ),
    ],
)
async def test_play_media_by_id(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    content_id: str,
    method: str,
    args: tuple,
) -> None:
    """Each addressable media type routes to the right client call."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: ENTITY,
            ATTR_MEDIA_CONTENT_TYPE: "music",
            ATTR_MEDIA_CONTENT_ID: content_id,
        },
        blocking=True,
    )
    getattr(mock_client, method).assert_awaited_once_with(*args)


async def test_play_media_by_search(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Search runs on the piano, which makes it the practical route for automations."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: ENTITY,
            ATTR_MEDIA_CONTENT_TYPE: "music",
            ATTR_MEDIA_CONTENT_ID: "search/Clair de lune",
        },
        blocking=True,
    )
    mock_client.async_play_search.assert_awaited_once_with("Clair de lune")


async def test_play_media_radio(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Radio channels are addressed by id."""
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: ENTITY,
            ATTR_MEDIA_CONTENT_TYPE: "music",
            ATTR_MEDIA_CONTENT_ID: "radio/5",
        },
        blocking=True,
    )
    mock_client.async_play_radio.assert_awaited_once_with(5)


@pytest.mark.parametrize(
    "content_id", ["nonsense", "song/not_a_group/1", "song/built_in_songs/not_an_int"]
)
async def test_play_media_rejects_bad_ids(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    content_id: str,
) -> None:
    """An unusable media id fails loudly rather than silently doing nothing."""
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            MP_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: ENTITY,
                ATTR_MEDIA_CONTENT_TYPE: "music",
                ATTR_MEDIA_CONTENT_ID: content_id,
            },
            blocking=True,
        )
    assert err.value.translation_key == "unsupported_media_id"


# ----------------------------------------------------------------------
# Browsing
# ----------------------------------------------------------------------


async def test_browse_root_lists_the_libraries(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The browser root offers every library, plus playlists and radio."""
    component = hass.data["entity_components"]["media_player"]
    entity = component.get_entity(ENTITY)
    root = await entity.async_browse_media()

    titles = [child.title for child in root.children]
    assert "Built-in Songs" in titles
    assert "Playlists" in titles
    assert "Radio" in titles
    assert root.can_expand is True
    assert root.can_play is False


async def test_browse_a_library_lists_songs(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Expanding a library lists playable songs."""
    component = hass.data["entity_components"]["media_player"]
    entity = component.get_entity(ENTITY)

    node = await entity.async_browse_media(media_content_id="library/built_in_songs")
    assert [child.title for child in node.children] == ["Angel", "Beyond the Sea"]
    assert all(child.can_play for child in node.children)


async def test_browse_unknown_id_raises(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Browsing something that is not a known node is an error, not an empty page."""
    component = hass.data["entity_components"]["media_player"]
    entity = component.get_entity(ENTITY)

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_browse_media(media_content_id="nonsense/thing")
    assert err.value.translation_key == "unsupported_media_id"
