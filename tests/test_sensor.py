"""Tests for the Disklavier song type sensor."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiodisklavier import (
    DisklavierConnectionError,
    DisklavierResponseError,
    LibrarySong,
    MasterState,
    SongFormat,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import setup_integration

ENTITY = "sensor.disklavier_pro_song_type"


async def test_song_type_reflects_the_loaded_song(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The default fixture song is PianoSoft PlusAudio, and says so.

    The attribute carries the library's speaker-path rule directly, which is the whole
    reason this sensor exists: an automation switches the receiver on it.
    """
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "plus_audio"
    assert state.attributes["audio_output"] is True


@pytest.mark.parametrize(
    ("format_", "expected", "audio"),
    [
        (SongFormat.SMF_SOLO, "solo", False),
        (SongFormat.SMF, "midi", False),
        (SongFormat.SMF_XG, "plus", True),
        (SongFormat.SMF_WAV, "plus_audio", True),
        (SongFormat.WAV, "audio", True),
    ],
)
async def test_each_format_maps_to_its_option(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    library_song: LibrarySong,
    format_: SongFormat,
    expected: str,
    audio: bool,
) -> None:
    """Every format the database can report has a sensor state."""
    mock_client.async_lookup_song.return_value = replace(library_song, format=format_)
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY)
    assert state.state == expected
    assert state.attributes["audio_output"] is audio


async def test_unknown_when_the_song_is_unresolvable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """No database entry for the loaded song reads as unknown, not a guess."""
    mock_client.async_lookup_song.return_value = None
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(ENTITY)
    assert state.state == "unknown"
    assert state.attributes["audio_output"] is None


async def test_unknown_when_the_format_is_new_to_us(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    library_song: LibrarySong,
) -> None:
    """A format this integration has never seen reads as unknown."""
    mock_client.async_lookup_song.return_value = replace(library_song, format=None)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY).state == "unknown"


async def test_unknown_when_extended_state_is_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Without master.json there is no song identity to look up."""
    mock_client.async_get_master_state.side_effect = DisklavierResponseError("nope")
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY).state == "unknown"
    assert mock_client.async_lookup_song.await_count == 0


async def test_unknown_when_the_database_read_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """A failing song database degrades this sensor and nothing else."""
    mock_client.async_lookup_song.side_effect = DisklavierResponseError("nope")
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY).state == "unknown"
    # The media player is untouched by the failure.
    assert hass.states.get("media_player.disklavier_pro").state == "paused"


async def test_a_reindex_rereads_the_song_database(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
) -> None:
    """A new library stamp re-reads the song database, once.

    The client only re-reads its cached database for a song it has never seen. A backing
    track synced beside an already indexed MIDI file changes that song's format without
    changing its id, so without this the sensor stayed on "midi" and the receiver off.
    """
    coordinator = init_integration.runtime_data
    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=1000
    )
    await coordinator.async_refresh()
    # The first stamp needs no read: the client fetches the database afresh on first use.
    mock_client.async_get_song_db.assert_not_awaited()

    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=2000
    )
    await coordinator.async_refresh()
    mock_client.async_get_song_db.assert_awaited_once_with(refresh=True)

    await coordinator.async_refresh()
    mock_client.async_get_song_db.assert_awaited_once_with(refresh=True)


async def test_a_failed_reread_is_retried_on_the_next_poll(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
) -> None:
    """A re-read that fails -- the file is rewritten during a reindex -- is retried."""
    coordinator = init_integration.runtime_data
    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=1000
    )
    await coordinator.async_refresh()

    mock_client.async_get_song_db.side_effect = DisklavierConnectionError("rewriting")
    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=2000
    )
    await coordinator.async_refresh()
    mock_client.async_get_song_db.side_effect = None
    await coordinator.async_refresh()
    assert mock_client.async_get_song_db.await_count == 2

    await coordinator.async_refresh()
    assert mock_client.async_get_song_db.await_count == 2
