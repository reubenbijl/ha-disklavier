"""Shared fixtures for the Disklavier integration tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, patch

import pytest
from aiodisklavier import (
    CurrentInfo,
    LibrarySong,
    MasterState,
    PlaybackStatus,
    PowerStatus,
    QuietMode,
    RepeatMode,
    Song,
    SongFormat,
    SongGroup,
    StaticInfo,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.disklavier.const import DOMAIN

HOST = "192.168.1.50"
DISKLAVIER_ID = "DKV000000000000"

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading this custom integration in every test."""
    return


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Force entities that ship disabled to register enabled.

    Home Assistant core provides this fixture to its own tests; the custom-component
    plugin does not re-export it, so it is defined here in the same terms. Needed to
    exercise the test-chord button, which is disabled by default because it makes a noise.
    """
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
    ):
        yield


@pytest.fixture
def static_info() -> StaticInfo:
    """Return device identity, as static_info reports it."""
    return StaticInfo(
        api_version="1.0",
        api_revision="1",
        disklavier_id=DISKLAVIER_ID,
        region="World",
        version="5.24.00",
        model="PRO",
        piano_type="grand",
    )


@pytest.fixture
def current_info() -> CurrentInfo:
    """Return live state: a song loaded and paused part-way through."""
    return CurrentInfo(
        power_status=PowerStatus.ON,
        quiet_status=QuietMode.ACOUSTIC,
        playback_status=PlaybackStatus.PAUSE,
        position_ms=516000,
        volume=100,
        song_title="Beethoven - Symphony No. 7, Movement 1.",
        song_artist="Ludwig van Beethoven",
        song_folder="Liszt's Beethoven's Symphony No. 7",
        duration_ms=851900,
    )


@pytest.fixture
def master_state() -> MasterState:
    """Return extended state, as the piano's internal endpoint reports it."""
    return MasterState(
        repeat=RepeatMode.OFF,
        headphone_connected=False,
        metronome_enabled=False,
        metronome_tempo=120,
        metronome_beat="4/4",
        key_motion=True,
        tempo=100,
        song_prefix="f",
        song_id=3608,
    )


@pytest.fixture
def library_song() -> LibrarySong:
    """Return the loaded song as the piano's database describes it."""
    return LibrarySong(
        prefix="f",
        song_id=3608,
        title="Beethoven - Symphony No. 7, Movement 1.",
        format=SongFormat.SMF_MP3,
        group=SongGroup.PC_SHARING_FOLDER,
        album_id=22,
        length_ms=851900,
        genre=None,
        composer="Ludwig van Beethoven",
        performer=None,
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the piano."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Disklavier PRO",
        data={CONF_HOST: HOST},
        unique_id=DISKLAVIER_ID,
    )


@pytest.fixture
def mock_client(
    static_info: StaticInfo,
    current_info: CurrentInfo,
    master_state: MasterState,
    library_song: LibrarySong,
) -> Generator[AsyncMock]:
    """Patch the Disklavier client everywhere the integration constructs one.

    Mocking at the library boundary rather than at HTTP is deliberate: aiodisklavier has
    its own HTTP-level tests, so repeating them here would test the same code twice and
    couple these tests to the wire format.
    """
    client = AsyncMock()
    client.host = HOST
    client.async_get_static_info.return_value = static_info
    client.async_get_current_info.return_value = current_info
    client.async_get_master_state.return_value = master_state
    client.async_get_songs.return_value = [
        Song(song_id=1, title="Angel"),
        Song(song_id=2, title="Beyond the Sea"),
    ]
    client.async_get_albums.return_value = []
    client.async_get_songs_in_album.return_value = []
    client.async_lookup_song.return_value = library_song
    client.async_search.return_value = []
    client.async_get_playlists.return_value = []
    client.async_get_playlist_items.return_value = []
    client.async_get_radio_channels.return_value = []

    with (
        patch("custom_components.disklavier.Disklavier", return_value=client) as mocked,
        patch(
            "custom_components.disklavier.config_flow.Disklavier", return_value=client
        ),
    ):
        mocked.return_value = client
        yield client


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration with a mocked piano, and unload it afterwards.

    The unload matters: commands schedule a delayed post-command refresh, and tearing
    the entry down cancels it the same way a real Home Assistant would.
    """
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    yield mock_config_entry
    if mock_config_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
