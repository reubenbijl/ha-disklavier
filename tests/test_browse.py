"""Tests for the media browser and the coordinator's failure paths."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiodisklavier import (
    Album,
    DisklavierConnectionError,
    DisklavierResponseError,
    LibraryAlbum,
    MasterState,
    Playlist,
    RadioChannel,
    Song,
    SongDatabase,
    SongGroup,
)
from homeassistant.components.media_player import BrowseMedia
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.disklavier.const import LIBRARY_SETTLE_SECONDS

from .conftest import share_db

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
    """A library with folders shows the folders, each opening in a single tap.

    Fetched from the piano's album list: genre collections in the built-in library,
    directories in the PC sharing folder. The flat song list is not consulted at all.
    A folder entry is not playable -- a playable card takes a touch screen two taps to
    open -- and the folder's own page plays it instead.
    """
    mock_client.async_get_albums.return_value = [
        Album(album_id=1, title="Pop"),
        Album(album_id=5, title=""),
    ]

    node = await _browse(hass, "library/built_in_songs")
    assert [child.title for child in node.children] == ["Pop", "(Root)"]
    assert node.children[0].media_content_id == "album/built_in_songs/1"
    assert not any(child.can_play for child in node.children)
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
    assert not any(child.can_play for child in level.children)

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
    """The playlist library lists playlists, each opening in a single tap like a folder."""
    mock_client.async_get_playlists.return_value = [
        Playlist(playlist_id=1, title="RR Christmas")
    ]

    node = await _browse(hass, "playlists/playlists")
    assert [child.title for child in node.children] == ["RR Christmas"]
    assert node.children[0].can_play is False
    assert node.children[0].can_expand is True

    # The playlist's own page is what plays it.
    page = await _browse(hass, "playlist/playlists/1")
    assert page.can_play is True


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


async def test_every_page_can_search(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Every page offers the media browser's search box; the entries on it do not.

    Home Assistant's media browser only draws its search box on a page that says it can
    search, so without this the ranked title search was unreachable from the browser.
    """
    mock_client.async_get_albums.return_value = [Album(album_id=9, title="Genre/Pop")]
    mock_client.async_get_playlists.return_value = [
        Playlist(playlist_id=1, title="RR Christmas")
    ]

    for content_id in (
        None,
        "quick_links",
        "quick_link/favourites",
        "library/built_in_songs",
        "album_dir/built_in_songs/Genre",
        "album/built_in_songs/9",
        "playlists/playlists",
        "playlist/playlists/1",
        "radio",
        "random",
    ):
        page = await _browse(hass, content_id)
        assert page.can_search is True, content_id
        assert not any(child.can_search for child in page.children), content_id


async def test_album_listing_is_read_once_per_library_change(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
) -> None:
    """Folder levels and album pages share one album listing until the piano reindexes.

    The piano takes about two seconds to build the listing for a few hundred albums, and
    it can only change with a reindex, which moves the library stamp.
    """
    mock_client.async_get_albums.return_value = [
        Album(album_id=2, title="HousePianistApp/Chopin")
    ]
    coordinator = init_integration.runtime_data
    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=1000
    )
    await coordinator.async_refresh()

    await _browse(hass, "library/pc_sharing_folder")
    await _browse(hass, "album_dir/pc_sharing_folder/HousePianistApp")
    page = await _browse(hass, "album/pc_sharing_folder/2")
    assert page.title == "Chopin"
    assert mock_client.async_get_albums.await_count == 1

    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=2000
    )
    await coordinator.async_refresh()
    await _browse(hass, "album_dir/pc_sharing_folder/HousePianistApp")
    assert mock_client.async_get_albums.await_count == 2


async def test_browse_quick_links_lists_the_configured_folders(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Quick Links offers one entry per configured folder, each opening in one tap."""
    root = await _browse(hass)
    assert "Quick Links" in [child.title for child in root.children]

    node = await _browse(hass, "quick_links")
    assert node.can_play is False
    assert [child.title for child in node.children] == ["To Review", "Favourites"]
    assert node.children[0].media_content_id == "quick_link/to-review"
    assert node.children[1].media_content_id == "quick_link/favourites"
    assert not any(child.can_play for child in node.children)
    assert all(child.can_expand for child in node.children)


async def test_browse_quick_link_jumps_straight_to_the_album(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A Quick Links entry skips PC Sharing Folder's own directory tree.

    Reaching Favourites any other way is PC Sharing Folder -> HousePianistApp ->
    Favourites; this is the one-tap alternative for the folder used every day. The
    folder is found in the song database, never in the album listing the piano takes
    several times longer to build.
    """
    mock_client.async_get_song_db.return_value = share_db(
        (1, "HousePianistApp/to-review"), (2, "HousePianistApp/Favourites")
    )
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]

    node = await _browse(hass, "quick_link/favourites")
    assert node.title == "Favourites"
    # The page itself is playable, which gives it a Play button for the whole folder,
    # and it keeps the link's own id: playing or picking it finds the folder by name
    # then, not by an album id that moves when the folder is recreated.
    assert node.can_play is True
    assert node.media_content_id == "quick_link/favourites"
    assert [child.title for child in node.children] == ["Clair de lune"]
    mock_client.async_get_songs_in_album.assert_awaited_once_with(
        2, SongGroup.PC_SHARING_FOLDER
    )
    mock_client.async_get_song_db.assert_awaited_once_with(refresh=True)
    mock_client.async_get_albums.assert_not_awaited()


async def test_browse_quick_link_page_is_titled_by_the_link(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A resolved page reads "To Review", like its entry, not the folder's raw name."""
    mock_client.async_get_song_db.return_value = share_db(
        (1, "HousePianistApp/to-review")
    )
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=90, title="Someone Like You")
    ]

    node = await _browse(hass, "quick_link/to-review")

    assert node.title == "To Review"
    assert node.media_content_id == "quick_link/to-review"


async def test_browse_quick_link_ignores_albums_in_other_libraries(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Only a PC Sharing Folder album answers to a Quick Links folder name.

    The song database describes every library at once, and a built-in collection
    could share a folder's name.
    """
    mock_client.async_get_song_db.return_value = SongDatabase(
        update=1,
        songs={},
        albums={
            "d9": LibraryAlbum(
                prefix="d",
                album_id=9,
                title="Favourites",
                path="preset/09_Favourites",
                group=SongGroup.BUILT_IN_SONGS,
            ),
            **share_db((2, "HousePianistApp/Favourites")).albums,
        },
    )

    await _browse(hass, "quick_link/favourites")

    mock_client.async_get_songs_in_album.assert_awaited_once_with(
        2, SongGroup.PC_SHARING_FOLDER
    )


async def test_browse_quick_link_falls_back_to_the_album_listing(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A song database with no album rows at all still finds the folder, the slow way."""
    mock_client.async_get_albums.return_value = [
        Album(album_id=2, title="HousePianistApp/Favourites")
    ]
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]

    node = await _browse(hass, "quick_link/favourites")

    assert [child.title for child in node.children] == ["Clair de lune"]
    mock_client.async_get_albums.assert_awaited_once_with(SongGroup.PC_SHARING_FOLDER)


async def test_browse_quick_link_before_the_folder_exists(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Browsing a Quick Links folder before it has ever been synced shows an empty page."""
    mock_client.async_get_song_db.return_value = share_db((5, "HousePianistApp/Chopin"))

    node = await _browse(hass, "quick_link/favourites")
    assert node.title == "Favourites"
    assert node.can_play is False
    assert node.children == []


async def test_browse_quick_link_again_skips_the_lookup(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Revisiting an unchanged Quick Links folder costs one song listing, nothing more.

    Listing one album's songs is the cheapest read the piano offers, and the page
    needs it regardless; the lookup behind it is not repeated.
    """
    mock_client.async_get_song_db.return_value = share_db(
        (2, "HousePianistApp/Favourites")
    )
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]

    await _browse(hass, "quick_link/favourites")
    node = await _browse(hass, "quick_link/favourites")

    assert [child.title for child in node.children] == ["Clair de lune"]
    assert mock_client.async_get_song_db.await_count == 1
    assert mock_client.async_get_songs_in_album.await_count == 2


async def test_browse_quick_link_looks_again_once_the_folder_changes(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Songs that differ from last time send the lookup back to the song database.

    A reindex can leave the remembered id naming a different folder entirely, so a
    listing that no longer matches is never assumed to be the same folder.
    """
    mock_client.async_get_song_db.return_value = share_db(
        (2, "HousePianistApp/Favourites")
    )
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]
    await _browse(hass, "quick_link/favourites")

    # Reindexed: Favourites is album 7 now, and album 2 belongs to another folder.
    mock_client.async_get_song_db.return_value = share_db(
        (2, "HousePianistApp/to-review"), (7, "HousePianistApp/Favourites")
    )
    listings = {
        2: [Song(song_id=90, title="Someone Like You")],
        7: [
            Song(song_id=43, title="Clair de lune"),
            Song(song_id=44, title="Gymnopédie No. 1"),
        ],
    }
    mock_client.async_get_songs_in_album.side_effect = lambda album_id, group: listings[
        album_id
    ]

    node = await _browse(hass, "quick_link/favourites")

    assert [child.title for child in node.children] == [
        "Clair de lune",
        "Gymnopédie No. 1",
    ]
    assert node.children[0].media_content_id == "song/pc_sharing_folder/43"
    assert mock_client.async_get_song_db.await_count == 2


async def test_browse_quick_link_recovers_when_its_album_id_is_gone(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A remembered album the piano no longer has sends the lookup back, every time.

    The piano answers "no album" for such an id, which the client raises rather than
    listing nothing -- when To Review is emptied by the keep flow and refilled, or a
    folder is renamed or recreated, it comes back under a new id.
    """
    mock_client.async_get_song_db.return_value = share_db(
        (2, "HousePianistApp/Favourites")
    )
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]
    await _browse(hass, "quick_link/favourites")

    # Favourites was recreated as album 7; album 2 no longer exists at all.
    mock_client.async_get_song_db.return_value = share_db(
        (7, "HousePianistApp/Favourites")
    )

    def listing(album_id: int, group: SongGroup) -> list[Song]:
        if album_id == 2:
            raise DisklavierResponseError(
                "Disklavier command 'get_song_list_in_album' failed: no album",
                command="get_song_list_in_album",
                error_info="no album",
            )
        return [Song(song_id=43, title="Clair de lune")]

    mock_client.async_get_songs_in_album.side_effect = listing

    node = await _browse(hass, "quick_link/favourites")
    assert [child.media_content_id for child in node.children] == [
        "song/pc_sharing_folder/43"
    ]

    # And the new album is what is remembered from then on.
    mock_client.async_get_song_db.reset_mock()
    await _browse(hass, "quick_link/favourites")
    mock_client.async_get_song_db.assert_not_awaited()


async def test_browse_quick_link_does_not_trust_an_empty_listing(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """An album that lists no songs is not remembered.

    An empty listing gives a later visit nothing to check the album against, so each
    visit looks the folder up again until it holds songs.
    """
    mock_client.async_get_song_db.return_value = share_db(
        (2, "HousePianistApp/Favourites")
    )
    mock_client.async_get_songs_in_album.return_value = []

    await _browse(hass, "quick_link/favourites")
    await _browse(hass, "quick_link/favourites")

    assert mock_client.async_get_song_db.await_count == 2


async def _report_library_stamp(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
    stamp: int,
    freezer: Any,
) -> None:
    """Have a poll report a library stamp, then let any refresh it schedules run."""
    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=stamp
    )
    await entry.runtime_data.async_refresh()
    freezer.tick(LIBRARY_SETTLE_SECONDS + 1)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_a_library_change_resolves_quick_links_before_anyone_looks(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
    freezer: Any,
) -> None:
    """A new library stamp resolves every Quick Links folder in the background.

    Every sync ends with a reindex, and the piano stamps its library when one
    finishes -- so a folder opened afterwards finds the lookup already done.
    """
    mock_client.async_get_song_db.return_value = share_db(
        (1, "HousePianistApp/to-review"), (2, "HousePianistApp/Favourites")
    )
    listings = {
        1: [Song(song_id=90, title="Someone Like You")],
        2: [Song(song_id=42, title="Clair de lune")],
    }
    mock_client.async_get_songs_in_album.side_effect = lambda album_id, group: listings[
        album_id
    ]

    await _report_library_stamp(
        hass, init_integration, mock_client, master_state, 2000, freezer
    )
    assert mock_client.async_get_song_db.await_count == 2
    mock_client.async_get_song_db.reset_mock()
    mock_client.async_get_songs_in_album.reset_mock()

    node = await _browse(hass, "quick_link/to-review")

    assert [child.title for child in node.children] == ["Someone Like You"]
    mock_client.async_get_song_db.assert_not_awaited()
    assert mock_client.async_get_songs_in_album.await_count == 1


async def test_the_background_refresh_waits_for_the_reindex_to_settle(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
    freezer: Any,
) -> None:
    """Nothing is read the moment the stamp moves, while listings can still be empty."""
    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=2000
    )
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    mock_client.async_get_song_db.assert_not_awaited()

    freezer.tick(LIBRARY_SETTLE_SECONDS + 1)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    mock_client.async_get_song_db.assert_awaited()


async def test_an_unchanged_library_stamp_is_not_refreshed_again(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
    freezer: Any,
) -> None:
    """Polls that report the same stamp leave Quick Links alone."""
    await _report_library_stamp(
        hass, init_integration, mock_client, master_state, 2000, freezer
    )
    reads = mock_client.async_get_song_db.await_count
    assert reads > 0

    await _report_library_stamp(
        hass, init_integration, mock_client, master_state, 2000, freezer
    )
    assert mock_client.async_get_song_db.await_count == reads


async def test_a_failed_background_refresh_leaves_the_folder_to_its_next_visit(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
    freezer: Any,
) -> None:
    """A refresh the piano cannot answer is dropped quietly, folder by folder."""
    mock_client.async_get_song_db.side_effect = DisklavierConnectionError("gone")

    await _report_library_stamp(
        hass, init_integration, mock_client, master_state, 2000, freezer
    )
    # The first folder's failure did not stop the second from being tried.
    assert mock_client.async_get_song_db.await_count == 2

    mock_client.async_get_song_db.side_effect = None
    mock_client.async_get_song_db.return_value = share_db(
        (2, "HousePianistApp/Favourites")
    )
    mock_client.async_get_songs_in_album.return_value = [
        Song(song_id=42, title="Clair de lune")
    ]
    node = await _browse(hass, "quick_link/favourites")
    assert [child.title for child in node.children] == ["Clair de lune"]


async def test_unload_cancels_a_pending_library_refresh(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    master_state: MasterState,
) -> None:
    """Unloading while a refresh waits for the reindex to settle drops it cleanly."""
    mock_client.async_get_master_state.return_value = replace(
        master_state, library_updated=2000
    )
    await init_integration.runtime_data.async_refresh()
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()


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
