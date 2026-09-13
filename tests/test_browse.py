"""Tests for the media browser and the coordinator's failure paths."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiodisklavier import (
    Album,
    DisklavierConnectionError,
    DisklavierResponseError,
    Playlist,
    RadioChannel,
    Song,
    SongGroup,
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


async def test_browse_a_library_lists_its_folders(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A library with folders shows the folders, each playable and expandable.

    Fetched from the piano's album list: genre collections in the built-in library,
    directories in the PC sharing folder. The flat song list is not consulted at all.
    """
    mock_client.async_get_albums.return_value = [
        Album(album_id=1, title="Pop"),
        Album(album_id=5, title=""),
    ]

    node = await _browse(hass, "library/built_in_songs")
    assert [child.title for child in node.children] == ["Pop", "(Root)"]
    assert node.children[0].media_content_id == "album/built_in_songs/1"
    assert all(child.can_play for child in node.children)
    assert all(child.can_expand for child in node.children)
    mock_client.async_get_songs.assert_not_awaited()


async def test_browse_inside_a_folder(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Expanding a folder lists its songs, under the folder's own name."""
    mock_client.async_get_albums.return_value = [
        Album(album_id=9, title="50 Greats for the Piano")
    ]
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=250, title="Invention 1")
    ]

    node = await _browse(hass, "album/built_in_songs/9")
    assert node.title == "50 Greats for the Piano"
    assert node.can_play is True
    assert [child.title for child in node.children] == ["Invention 1"]
    assert node.children[0].media_content_id == "song/built_in_songs/250"
    mock_client.async_get_songs_in_album.assert_awaited_once_with(
        9, SongGroup.BUILT_IN_SONGS
    )


async def test_path_titled_folders_become_nested_directories(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Albums titled with / separators split into a virtual directory tree.

    The piano's indexer flattens nested directories into path-like album titles, so
    one level of the browse tree shows one path segment.
    """
    mock_client.async_get_albums.return_value = [
        Album(album_id=1, title="Pop"),
        Album(album_id=2, title="ImpromptuApp/Alban Berg"),
        Album(album_id=3, title="ImpromptuApp/Chopin"),
        Album(album_id=4, title="Deep/A/B"),
        Album(album_id=5, title=""),
    ]

    root = await _browse(hass, "library/pc_sharing_folder")
    assert [child.title for child in root.children] == [
        "Pop",
        "ImpromptuApp",
        "Deep",
        "(Root)",
    ]
    impromptu = root.children[1]
    assert impromptu.media_content_id == "album_dir/pc_sharing_folder/ImpromptuApp"
    assert impromptu.can_play is False
    assert impromptu.can_expand is True

    level = await _browse(hass, "album_dir/pc_sharing_folder/ImpromptuApp")
    assert level.title == "ImpromptuApp"
    assert level.can_play is False
    assert [child.title for child in level.children] == ["Alban Berg", "Chopin"]
    assert level.children[0].media_content_id == "album/pc_sharing_folder/2"
    assert all(child.can_play for child in level.children)

    deep = await _browse(hass, "album_dir/pc_sharing_folder/Deep")
    assert [child.title for child in deep.children] == ["A"]
    assert deep.children[0].media_content_id == "album_dir/pc_sharing_folder/Deep/A"

    leaf_level = await _browse(hass, "album_dir/pc_sharing_folder/Deep/A")
    assert [child.title for child in leaf_level.children] == ["B"]
    assert leaf_level.children[0].media_content_id == "album/pc_sharing_folder/4"


async def test_an_album_named_like_a_directory_is_listed_in_it(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """An album whose full title equals a virtual directory path stays reachable."""
    mock_client.async_get_albums.return_value = [
        Album(album_id=1, title="X"),
        Album(album_id=2, title="X/Y"),
    ]

    root = await _browse(hass, "library/pc_sharing_folder")
    assert [child.title for child in root.children] == ["X", "X"]

    level = await _browse(hass, "album_dir/pc_sharing_folder/X")
    assert [(child.title, child.media_content_id) for child in level.children] == [
        ("X", "album/pc_sharing_folder/1"),
        ("Y", "album/pc_sharing_folder/2"),
    ]


async def test_browse_album_shows_the_last_path_segment(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A path-titled album's own page is named by its final segment."""
    mock_client.async_get_albums.return_value = [
        Album(album_id=7, title="ImpromptuApp/Alban Berg")
    ]
    mock_client.async_get_songs_in_album.return_value = []

    node = await _browse(hass, "album/pc_sharing_folder/7")
    assert node.title == "Alban Berg"


@pytest.mark.parametrize("albums", [[Album(album_id=5, title="")], []])
async def test_browse_an_unnamed_folder(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    albums: list[Album],
) -> None:
    """A folder with no name still gets a readable one.

    The piano keeps a library's root-level files in an album with an empty title, and a
    stale id may name a folder the piano no longer reports at all.
    """
    mock_client.async_get_albums.return_value = albums

    node = await _browse(hass, "album/pc_sharing_folder/5")
    assert node.title == "(Root)"


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
        ("library/built_in_songs", "async_get_albums"),
        ("library/built_in_songs", "async_get_songs"),
        ("album/built_in_songs/9", "async_get_songs_in_album"),
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


async def test_browse_quick_links_lists_the_configured_folders(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Quick Links offers one playable, expandable entry per configured folder."""
    root = await _browse(hass)
    assert "Quick Links" in [child.title for child in root.children]

    node = await _browse(hass, "quick_links")
    assert node.can_play is False
    assert [child.title for child in node.children] == ["To Review", "Favourites"]
    assert node.children[0].media_content_id == "quick_link/to-review"
    assert node.children[1].media_content_id == "quick_link/favourites"
    assert all(child.can_play for child in node.children)
    assert all(child.can_expand for child in node.children)


async def test_browse_quick_link_jumps_straight_to_the_album(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A Quick Links entry skips PC Sharing Folder's own directory tree.

    Reaching Favourites any other way is PC Sharing Folder -> HousePianistApp ->
    Favourites; this is the one-tap alternative for the folder used every day.
    """
    mock_client.async_get_albums.return_value = [
        Album(album_id=1, title="HousePianistApp/to-review"),
        Album(album_id=2, title="HousePianistApp/Favourites"),
    ]
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]

    node = await _browse(hass, "quick_link/favourites")
    assert node.title == "Favourites"
    assert node.can_play is True
    assert [child.title for child in node.children] == ["Clair de lune"]
    mock_client.async_get_songs_in_album.assert_awaited_once_with(
        2, SongGroup.PC_SHARING_FOLDER
    )
    # The album list is the slow call; the page must not fetch it a second time just
    # to recover a title the lookup already had.
    assert mock_client.async_get_albums.await_count == 1


async def test_browse_quick_link_before_the_folder_exists(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Browsing a Quick Links folder before it has ever been synced shows an empty page."""
    mock_client.async_get_albums.return_value = []

    node = await _browse(hass, "quick_link/favourites")
    assert node.title == "Favourites"
    assert node.can_play is False
    assert node.children == []


async def test_browse_quick_link_again_skips_the_album_list(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Revisiting an unchanged Quick Links folder costs one song listing, nothing more.

    Listing every album takes the piano about two seconds with a few hundred of them;
    one album's songs take a tenth of that, and the page needs them regardless.
    """
    mock_client.async_get_albums.return_value = [
        Album(album_id=2, title="HousePianistApp/Favourites")
    ]
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]

    await _browse(hass, "quick_link/favourites")
    node = await _browse(hass, "quick_link/favourites")

    assert [child.title for child in node.children] == ["Clair de lune"]
    assert mock_client.async_get_albums.await_count == 1
    assert mock_client.async_get_songs_in_album.await_count == 2


async def test_browse_quick_link_looks_again_once_the_folder_changes(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Songs that differ from last time send the lookup back to the album list.

    A reindex can leave the remembered id naming a different folder entirely, so a
    listing that no longer matches is never assumed to be the same folder.
    """
    mock_client.async_get_albums.return_value = [
        Album(album_id=2, title="HousePianistApp/Favourites")
    ]
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]
    await _browse(hass, "quick_link/favourites")

    # Reindexed: Favourites is album 7 now, and album 2 belongs to another folder.
    mock_client.async_get_albums.return_value = [
        Album(album_id=2, title="HousePianistApp/to-review"),
        Album(album_id=7, title="HousePianistApp/Favourites"),
    ]
    listings = {
        2: [Song(song_id=90, title="Someone Like You")],
        7: [
            Song(song_id=43, title="Clair de lune"),
            Song(song_id=44, title="Gymnopédie No. 1"),
        ],
    }
    mock_client.async_get_songs_in_album.side_effect = (
        lambda album_id, group: listings[album_id]
    )

    node = await _browse(hass, "quick_link/favourites")

    assert [child.title for child in node.children] == [
        "Clair de lune",
        "Gymnopédie No. 1",
    ]
    assert node.children[0].media_content_id == "song/pc_sharing_folder/43"
    assert mock_client.async_get_albums.await_count == 2


async def test_browse_quick_link_does_not_trust_an_empty_listing(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """An album that lists no songs is not remembered.

    A stale id also lists no songs, so an empty result could never tell the two
    apart -- right after a reindex the piano briefly answers this way for real albums.
    """
    mock_client.async_get_albums.return_value = [
        Album(album_id=2, title="HousePianistApp/Favourites")
    ]
    mock_client.async_get_songs_in_album.return_value = []

    await _browse(hass, "quick_link/favourites")
    await _browse(hass, "quick_link/favourites")

    assert mock_client.async_get_albums.await_count == 2


async def test_browse_surprise_me_lists_genres(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Surprise Me offers one playable random pick per built-in genre."""
    root = await _browse(hass)
    assert "Surprise Me" in [child.title for child in root.children]

    node = await _browse(hass, "random")
    assert node.can_play is False
    assert len(node.children) == 11
    jazz = next(child for child in node.children if child.title == "Jazz")
    assert jazz.media_content_id == "random/jazz"
    assert jazz.can_play is True
    assert jazz.can_expand is False


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
