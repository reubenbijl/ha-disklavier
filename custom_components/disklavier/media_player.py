"""Media player entity for the Yamaha Disklavier integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from aiodisklavier import (
    VOLUME_MAX,
    Album,
    DisklavierError,
    Genre,
    GenreSelect,
    PlaylistGroup,
    PowerStatus,
    SearchKind,
    Song,
    SongGroup,
)
from aiodisklavier import RepeatMode as DkvRepeat
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import (
    CONTENT_ALBUM,
    CONTENT_PLAYLIST,
    CONTENT_PLAYLIST_ITEM,
    CONTENT_QUICK_LINK,
    CONTENT_QUICK_LINKS,
    CONTENT_RADIO,
    CONTENT_RANDOM,
    CONTENT_SEARCH,
    CONTENT_SONG,
    DOMAIN,
    LIBRARY_SETTLE_SECONDS,
    MS_PER_SECOND,
)
from .coordinator import DisklavierConfigEntry, DisklavierCoordinator
from .entity import DisklavierEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

#: Libraries offered in the media browser, in the order they appear.
_SONG_LIBRARIES: list[tuple[SongGroup, str]] = [
    (SongGroup.BUILT_IN_SONGS, "Built-in Songs"),
    (SongGroup.MY_RECORDINGS, "My Recordings"),
    (SongGroup.DOWNLOADED_SONGS, "Downloaded Songs"),
    (SongGroup.MY_SONGS, "My Songs"),
    (SongGroup.PC_SHARING_FOLDER, "PC Sharing Folder"),
]

_PLAYLIST_LIBRARIES: list[tuple[PlaylistGroup, str]] = [
    (PlaylistGroup.PLAYLISTS, "Playlists"),
    (PlaylistGroup.DEMO_PLAYLIST, "Demo Playlist"),
]

#: Shown for the piano's unnamed album, which holds the files at a library's root.
#: "(Root)" is what Yamaha's own ENSPIRE controller calls it.
_UNNAMED_FOLDER = "(Root)"

#: PC Sharing Folder subdirectories offered under "Quick Links", in display order.
#: Each jumps straight to that folder's own page, bypassing PC Sharing Folder's own
#: directory tree entirely. Matched case-insensitively against an album's last path
#: segment -- never by id, since a reindex reassigns those. Add a folder here to give
#: it the same one-tap shortcut.
_QUICK_LINKS: list[tuple[str, str]] = [
    ("to-review", "To Review"),
    ("favourites", "Favourites"),
]

#: Built-in genres offered under "Surprise Me", in the piano's own menu order. Each
#: node asks the piano itself to pick a random song from that genre.
_RANDOM_GENRES: list[tuple[Genre, str]] = [
    (Genre.POP, "Pop"),
    (Genre.ROCK, "Rock"),
    (Genre.JAZZ, "Jazz"),
    (Genre.RNB_SOUL, "R&B / Soul"),
    (Genre.CLASSICAL, "Classical"),
    (Genre.COUNTRY, "Country"),
    (Genre.HOLIDAYS, "Holidays"),
    (Genre.SOUNDTRACK, "Soundtrack"),
    (Genre.PIANO50, "50 Greats for the Piano"),
    (Genre.LESSON, "Lesson"),
    (Genre.SMARTKEY, "SmartKey"),
]

#: Disklavier repeat mode -> (Home Assistant repeat mode, shuffle).
_REPEAT_TO_HA: dict[DkvRepeat, tuple[RepeatMode, bool]] = {
    DkvRepeat.OFF: (RepeatMode.OFF, False),
    DkvRepeat.ONE: (RepeatMode.ONE, False),
    DkvRepeat.MEDIA_ALL: (RepeatMode.ALL, False),
    DkvRepeat.ALBUM_ALL: (RepeatMode.ALL, False),
    DkvRepeat.PLAYLIST_ALL: (RepeatMode.ALL, False),
    DkvRepeat.MEDIA_SHUFFLE: (RepeatMode.ALL, True),
    DkvRepeat.ALBUM_SHUFFLE: (RepeatMode.ALL, True),
    DkvRepeat.PLAYLIST_SHUFFLE: (RepeatMode.ALL, True),
}


def _to_disklavier_repeat(repeat: RepeatMode, shuffle: bool) -> DkvRepeat:
    """Fold Home Assistant's separate repeat and shuffle into one Disklavier mode.

    The piano has no way to shuffle without repeating, so shuffle wins where they conflict.
    """
    if shuffle:
        return DkvRepeat.MEDIA_SHUFFLE
    if repeat is RepeatMode.ONE:
        return DkvRepeat.ONE
    if repeat is RepeatMode.ALL:
        return DkvRepeat.MEDIA_ALL
    return DkvRepeat.OFF


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DisklavierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Disklavier media player."""
    async_add_entities([DisklavierMediaPlayer(entry.runtime_data)])


class DisklavierMediaPlayer(DisklavierEntity, MediaPlayerEntity):
    """A Disklavier as a media player."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.REPEAT_SET
        | MediaPlayerEntityFeature.SHUFFLE_SET
        | MediaPlayerEntityFeature.SEARCH_MEDIA
    )

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the media player."""
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.static_info.disklavier_id
        self._optimistic_state: MediaPlayerState | None = None
        #: Quick Links folder -> the album it resolved to, and the songs it held then.
        #: See ``_resolve_quick_link``.
        self._quick_links: dict[str, tuple[Album, frozenset[Song]]] = {}
        #: The piano's library stamp as of the last Quick Links refresh it scheduled.
        self._library_seen: int | None = None
        self._cancel_warm_up: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _set_optimistic_state(self, state: MediaPlayerState) -> None:
        """Show a transport command's expected outcome immediately.

        The firmware reports the previous state for a moment after accepting a command,
        so waiting for a poll leaves the button visibly lagging what the piano is
        audibly doing. The next coordinator update clears this and the polled truth
        wins.
        """
        self._optimistic_state = state
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Let each real poll supersede any optimistic state."""
        self._optimistic_state = None
        self._watch_library()
        super()._handle_coordinator_update()

    @callback
    def _watch_library(self) -> None:
        """Refresh Quick Links soon after the piano's library changes.

        Every sync ends with a reindex, and the piano stamps its library when one
        finishes -- ``MasterState.library_updated``, which arrives with the poll that
        already runs. Refreshing then moves the folder lookup to just after the sync,
        so opening a folder later finds it already resolved. The first stamp seen,
        at startup, counts as a change.
        """
        master = self.coordinator.data.master
        stamp = None if master is None else master.library_updated
        if stamp is None or stamp == self._library_seen:
            return
        self._library_seen = stamp
        if self._cancel_warm_up is not None:
            self._cancel_warm_up()
        self._cancel_warm_up = async_call_later(
            self.hass, LIBRARY_SETTLE_SECONDS, self._async_warm_quick_links
        )

    async def _async_warm_quick_links(self, _now: datetime) -> None:
        """Resolve every Quick Links folder now, so the next visit is already warm."""
        self._cancel_warm_up = None
        for folder, _ in _QUICK_LINKS:
            try:
                await self._resolve_quick_link(folder)
            except DisklavierError as err:
                # Nothing is lost: the next visit to the folder does the lookup itself.
                _LOGGER.debug("Could not refresh quick link %s: %s", folder, err)

    async def async_will_remove_from_hass(self) -> None:
        """Drop any pending Quick Links refresh."""
        if self._cancel_warm_up is not None:
            self._cancel_warm_up()
            self._cancel_warm_up = None
        await super().async_will_remove_from_hass()

    @property
    def state(self) -> MediaPlayerState:
        """Return the player state.

        ``wakeup`` is reported as off: the piano is still ~12 seconds from accepting
        commands, so presenting it as on would invite failures.
        """
        if self._optimistic_state is not None:
            return self._optimistic_state
        current = self.coordinator.data.current
        if current.power_status in (PowerStatus.SLEEP, PowerStatus.WAKEUP):
            return MediaPlayerState.OFF
        if current.is_playing:
            return MediaPlayerState.PLAYING
        # The piano has no stop state; a zero position is the only way to tell that
        # 'stop' was used rather than 'pause'.
        if current.is_stopped:
            return MediaPlayerState.IDLE
        return MediaPlayerState.PAUSED

    @property
    def volume_level(self) -> float | None:
        """Return the volume, scaled to 0..1 for Home Assistant."""
        volume = self.coordinator.data.current.volume
        return None if volume is None else volume / VOLUME_MAX

    @property
    def media_title(self) -> str | None:
        """Return the current song title."""
        return self.coordinator.data.current.song_title

    @property
    def media_artist(self) -> str | None:
        """Return the current song's artist."""
        return self.coordinator.data.current.song_artist

    @property
    def media_album_name(self) -> str | None:
        """Return the folder or album the current song belongs to."""
        return self.coordinator.data.current.song_folder

    @property
    def media_duration(self) -> int | None:
        """Return the song length in seconds."""
        duration = self.coordinator.data.current.duration_seconds
        return None if duration is None else int(duration)

    @property
    def media_position(self) -> int | None:
        """Return the playback position in seconds."""
        position = self.coordinator.data.current.position_seconds
        return None if position is None else int(position)

    @property
    def media_position_updated_at(self) -> datetime:
        """Return when the position was last read, so the UI can extrapolate."""
        return self.coordinator.data.fetched_at

    @property
    def repeat(self) -> RepeatMode | None:
        """Return the repeat mode."""
        master = self.coordinator.data.master
        if master is None or master.repeat is None:
            return None
        return _REPEAT_TO_HA.get(master.repeat, (RepeatMode.OFF, False))[0]

    @property
    def shuffle(self) -> bool | None:
        """Return whether shuffle is on."""
        master = self.coordinator.data.master
        if master is None or master.repeat is None:
            return None
        return _REPEAT_TO_HA.get(master.repeat, (RepeatMode.OFF, False))[1]

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_media_play(self) -> None:
        """Start playback."""
        await self._async_call(self.coordinator.client.async_play())
        self._set_optimistic_state(MediaPlayerState.PLAYING)

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._async_call(self.coordinator.client.async_pause())
        self._set_optimistic_state(MediaPlayerState.PAUSED)

    async def async_media_stop(self) -> None:
        """Stop playback and rewind."""
        await self._async_call(self.coordinator.client.async_stop())
        self._set_optimistic_state(MediaPlayerState.IDLE)

    async def async_media_next_track(self) -> None:
        """Skip to the next song."""
        await self._async_call(self.coordinator.client.async_next_song())

    async def async_media_previous_track(self) -> None:
        """Go back to the previous song."""
        await self._async_call(self.coordinator.client.async_previous_song())

    async def async_media_seek(self, position: float) -> None:
        """Seek to a position, in seconds."""
        await self._async_call(
            self.coordinator.client.async_seek(int(position * MS_PER_SECOND))
        )

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the volume from a 0..1 value."""
        await self._async_call(
            self.coordinator.client.async_set_volume(round(volume * VOLUME_MAX))
        )

    async def async_volume_up(self) -> None:
        """Step the volume up."""
        await self._async_call(self.coordinator.client.async_volume_up())

    async def async_volume_down(self) -> None:
        """Step the volume down."""
        await self._async_call(self.coordinator.client.async_volume_down())

    async def async_turn_on(self) -> None:
        """Wake the piano from standby."""
        await self._async_call(self.coordinator.client.async_turn_on())

    async def async_turn_off(self) -> None:
        """Send the piano to standby."""
        await self._async_call(self.coordinator.client.async_turn_off())

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set the repeat mode, preserving the current shuffle setting."""
        await self._async_call(
            self.coordinator.client.async_set_repeat(
                _to_disklavier_repeat(repeat, bool(self.shuffle))
            )
        )

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Turn shuffle on or off, preserving the current repeat mode."""
        await self._async_call(
            self.coordinator.client.async_set_repeat(
                _to_disklavier_repeat(self.repeat or RepeatMode.OFF, shuffle)
            )
        )

    # ------------------------------------------------------------------
    # Playing media
    # ------------------------------------------------------------------

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play an item chosen in the media browser, or addressed directly.

        Accepted ``media_id`` forms::

            song/<group>/<id>
            album/<group>/<id>
            playlist/<group>/<id>
            playlist_item/<group>/<id>
            radio/<channel_id>
            random/<genre>
            search/<title>
            quick_link/<folder>
        """
        client = self.coordinator.client
        kind, _, rest = media_id.partition("/")

        try:
            if kind == CONTENT_SEARCH:
                await self._async_call(client.async_play_search(rest))
                return
            if kind == CONTENT_RANDOM:
                await self._async_call(
                    client.async_play_genre(Genre(rest), select=GenreSelect.RANDOM)
                )
                return
            if kind == CONTENT_RADIO:
                await self._async_call(client.async_play_radio(int(rest)))
                return
            if kind == CONTENT_QUICK_LINK:
                await self._async_play_quick_link(rest)
                return

            group_name, _, item_id = rest.partition("/")
            if kind == CONTENT_SONG:
                await self._async_call(
                    client.async_play_song(int(item_id), SongGroup(group_name))
                )
            elif kind == CONTENT_ALBUM:
                await self._async_call(
                    client.async_play_album(int(item_id), SongGroup(group_name))
                )
            elif kind == CONTENT_PLAYLIST:
                await self._async_call(
                    client.async_play_playlist(int(item_id), PlaylistGroup(group_name))
                )
            elif kind == CONTENT_PLAYLIST_ITEM:
                await self._async_call(
                    client.async_play_playlist_item(
                        int(item_id), PlaylistGroup(group_name)
                    )
                )
            else:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="unsupported_media_id",
                    translation_placeholders={"media_id": media_id},
                )
        except (ValueError, KeyError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unsupported_media_id",
                translation_placeholders={"media_id": media_id},
            ) from err

    # ------------------------------------------------------------------
    # Browsing
    # ------------------------------------------------------------------

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse the piano's libraries."""
        if media_content_id in (None, "", "root"):
            return self._browse_root()

        assert media_content_id is not None
        kind, _, rest = media_content_id.partition("/")

        try:
            if kind == "library":
                return await self._browse_song_library(SongGroup(rest))
            if kind == "album_dir":
                group_name, _, dir_path = rest.partition("/")
                return await self._browse_album_dir(SongGroup(group_name), dir_path)
            if kind == CONTENT_ALBUM:
                group_name, _, album_id = rest.partition("/")
                return await self._browse_album(SongGroup(group_name), int(album_id))
            if kind == "playlists":
                return await self._browse_playlist_library(PlaylistGroup(rest))
            if kind == CONTENT_PLAYLIST:
                group_name, _, playlist_id = rest.partition("/")
                return await self._browse_playlist(
                    PlaylistGroup(group_name), int(playlist_id)
                )
            if kind == CONTENT_RADIO:
                return await self._browse_radio()
            if kind == CONTENT_RANDOM:
                return self._browse_random()
            if kind == CONTENT_QUICK_LINKS:
                return self._browse_quick_links()
            if kind == CONTENT_QUICK_LINK:
                return await self._browse_quick_link(rest)
        except DisklavierError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="browse_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unsupported_media_id",
                translation_placeholders={"media_id": str(media_content_id)},
            ) from err

        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unsupported_media_id",
            translation_placeholders={"media_id": str(media_content_id)},
        )

    def _browse_root(self) -> BrowseMedia:
        """Build the top level of the browser."""
        children = [
            BrowseMedia(
                title="Quick Links",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.MUSIC,
                media_content_id=CONTENT_QUICK_LINKS,
                can_play=False,
                can_expand=True,
            )
        ]
        children += [
            BrowseMedia(
                title=title,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.MUSIC,
                media_content_id=f"library/{group.value}",
                can_play=False,
                can_expand=True,
            )
            for group, title in _SONG_LIBRARIES
        ]
        children += [
            BrowseMedia(
                title=title,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.PLAYLIST,
                media_content_id=f"playlists/{group.value}",
                can_play=False,
                can_expand=True,
            )
            for group, title in _PLAYLIST_LIBRARIES
        ]
        children.append(
            BrowseMedia(
                title="Radio",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.MUSIC,
                media_content_id=CONTENT_RADIO,
                can_play=False,
                can_expand=True,
            )
        )
        children.append(
            BrowseMedia(
                title="Surprise Me",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.MUSIC,
                media_content_id=CONTENT_RANDOM,
                can_play=False,
                can_expand=True,
            )
        )
        return BrowseMedia(
            title="Disklavier",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            media_content_id="root",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_song_library(self, group: SongGroup) -> BrowseMedia:
        """List one library: its folders, or its songs where it reports no folders.

        The piano files every library into albums -- genre collections in the built-in
        library, directories in the PC sharing folder, "Recorded Songs" and "Kept Songs"
        for recordings -- so those are what browsing a library shows. A library with no
        albums is listed flat. An empty library comes back as an empty list from the
        client, so anything raising here is a real fault and is reported as one.
        """
        albums = await self.coordinator.client.async_get_albums(group)

        children: list[BrowseMedia]
        if albums:
            children_class = MediaClass.DIRECTORY
            children = self._album_level_nodes(group, albums, "")
        else:
            children_class = MediaClass.TRACK
            children = self._song_nodes(
                group, await self.coordinator.client.async_get_songs(group)
            )

        return BrowseMedia(
            title=dict(_SONG_LIBRARIES).get(group, group.value),
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            media_content_id=f"library/{group.value}",
            can_play=False,
            can_expand=True,
            children_media_class=children_class,
            children=children,
        )

    async def _browse_album_dir(self, group: SongGroup, path: str) -> BrowseMedia:
        """List one level of the virtual folder tree within a library.

        These levels have no ids of their own on the piano; they exist only as the
        ``/``-separated prefixes of album titles.
        """
        albums = await self.coordinator.client.async_get_albums(group)

        return BrowseMedia(
            title=path.rsplit("/", 1)[-1],
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            media_content_id=f"album_dir/{group.value}/{path}",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
            children=self._album_level_nodes(group, albums, path),
        )

    def _album_level_nodes(
        self, group: SongGroup, albums: list[Album], path: str
    ) -> list[BrowseMedia]:
        """Build one level of a library's folder tree.

        The piano flattens nested directories into album titles with ``/`` separators
        (``ImpromptuApp/Alban Berg``), so titles are split back into levels: an album
        whose remaining title holds no separator is a folder of songs, and every
        distinct leading segment becomes a virtual directory, kept in the piano's own
        ordering at first appearance.

        No folder entry in a listing is marked playable -- only a folder's own page is,
        which puts a Play button in its header. Home Assistant's media browser hides a
        playable card's play button until the pointer hovers, and a touch screen spends
        the first tap revealing it, so playable folders took two taps to open where
        every other folder took one, with nothing on screen to tell them apart.
        """
        prefix = f"{path}/" if path else ""
        seen_dirs: set[str] = set()
        nodes: list[BrowseMedia] = []

        for album in albums:
            title = album.title
            if path and title == path:
                rest = title.rsplit("/", 1)[-1]
            elif title.startswith(prefix):
                rest = title[len(prefix) :]
            else:
                continue

            head, sep, _ = rest.partition("/")
            if sep:
                if head not in seen_dirs:
                    seen_dirs.add(head)
                    dir_path = f"{prefix}{head}"
                    nodes.append(
                        BrowseMedia(
                            title=head,
                            media_class=MediaClass.DIRECTORY,
                            media_content_type=MediaType.MUSIC,
                            media_content_id=f"album_dir/{group.value}/{dir_path}",
                            can_play=False,
                            can_expand=True,
                        )
                    )
            else:
                nodes.append(
                    BrowseMedia(
                        title=head or _UNNAMED_FOLDER,
                        media_class=MediaClass.DIRECTORY,
                        media_content_type=MediaType.MUSIC,
                        media_content_id=(
                            f"{CONTENT_ALBUM}/{group.value}/{album.album_id}"
                        ),
                        can_play=False,
                        can_expand=True,
                    )
                )
        return nodes

    async def _browse_album(self, group: SongGroup, album_id: int) -> BrowseMedia:
        """List the songs inside one folder of a library."""
        albums = await self.coordinator.client.async_get_albums(group)
        songs = await self.coordinator.client.async_get_songs_in_album(album_id, group)
        title = next((a.title for a in albums if a.album_id == album_id), "")
        return self._album_node(group, album_id, title, songs)

    def _album_node(
        self, group: SongGroup, album_id: int, title: str, songs: list[Song]
    ) -> BrowseMedia:
        """Build one folder's page from an album title and songs already in hand."""
        # Path-titled albums ("ImpromptuApp/Alban Berg") show just their last segment;
        # the parents are rendered as the virtual folder levels above this page.
        title = title.rsplit("/", 1)[-1]

        return BrowseMedia(
            title=title or _UNNAMED_FOLDER,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            media_content_id=f"{CONTENT_ALBUM}/{group.value}/{album_id}",
            can_play=True,
            can_expand=True,
            children_media_class=MediaClass.TRACK,
            children=self._song_nodes(group, songs),
        )

    def _browse_quick_links(self) -> BrowseMedia:
        """List the curated one-tap shortcuts onto specific PC Sharing Folder folders.

        Each entry opens the folder's page, same as a folder found by browsing PC
        Sharing Folder directly -- this is just a shorter path there. Like every
        folder entry it is not itself playable; see ``_album_level_nodes``.
        """
        return BrowseMedia(
            title="Quick Links",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            media_content_id=CONTENT_QUICK_LINKS,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
            children=[
                BrowseMedia(
                    title=title,
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.MUSIC,
                    media_content_id=f"{CONTENT_QUICK_LINK}/{folder}",
                    can_play=False,
                    can_expand=True,
                )
                for folder, title in _QUICK_LINKS
            ],
        )

    async def _find_album_by_folder_name(self, folder: str) -> Album | None:
        """Look a PC Sharing Folder subdirectory up by name.

        Read from a fresh copy of the piano's song database, which it serves in about
        a third of a second, rather than its album listing, which it takes nearer two
        to build for a few hundred albums -- same ids, same titles. A database with no
        album rows at all falls back to the listing.
        """
        client = self.coordinator.client
        db = await client.async_get_song_db(refresh=True)
        albums: list[Album]
        if db.albums:
            albums = [
                Album(album_id=album.album_id, title=album.title)
                for album in db.albums.values()
                if album.group is SongGroup.PC_SHARING_FOLDER
            ]
        else:
            albums = await client.async_get_albums(SongGroup.PC_SHARING_FOLDER)
        return next(
            (
                album
                for album in albums
                if album.title.rsplit("/", 1)[-1].casefold() == folder.casefold()
            ),
            None,
        )

    async def _resolve_quick_link(self, folder: str) -> tuple[Album, list[Song]] | None:
        """Resolve a Quick Links folder to its album and the songs in it now.

        Listing one album's songs is the cheapest read there is, and the page needs
        it anyway, so the album is remembered and each visit lists the remembered
        album's songs and compares them with last time's. The same songs under the
        same ids can only have come from the same folder, so the album still stands;
        anything else -- songs synced in, a folder moved, ids reassigned by a
        reindex -- goes back through the full lookup. ``_watch_library`` runs this
        after every reindex, so a visit normally finds the work already done.
        """
        client = self.coordinator.client
        group = SongGroup.PC_SHARING_FOLDER

        remembered = self._quick_links.get(folder)
        if remembered is not None:
            album, seen = remembered
            songs = await client.async_get_songs_in_album(album.album_id, group)
            if frozenset(songs) == seen:
                return album, songs

        found = await self._find_album_by_folder_name(folder)
        if found is None:
            self._quick_links.pop(folder, None)
            return None
        songs = await client.async_get_songs_in_album(found.album_id, group)
        if songs:
            self._quick_links[folder] = (found, frozenset(songs))
        else:
            # An empty listing is also what a stale id gets back, so it proves nothing.
            self._quick_links.pop(folder, None)
        return found, songs

    async def _browse_quick_link(self, folder: str) -> BrowseMedia:
        """Jump straight into one Quick Links folder.

        Without this, reaching e.g. Favourites means PC Sharing Folder ->
        HousePianistApp -> Favourites -- three taps to the one folder that matters
        most day to day.
        """
        title = next((t for f, t in _QUICK_LINKS if f == folder), folder)
        resolved = await self._resolve_quick_link(folder)
        if resolved is None:
            return BrowseMedia(
                title=title,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.MUSIC,
                media_content_id=f"{CONTENT_QUICK_LINK}/{folder}",
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.TRACK,
                children=[],
            )
        album, songs = resolved
        return self._album_node(
            SongGroup.PC_SHARING_FOLDER, album.album_id, album.title, songs
        )

    async def _async_play_quick_link(self, folder: str) -> None:
        """Play one Quick Links folder, resolving its current album id first.

        The lookup itself can fail the same way any piano request can, so it gets
        the same translated-error treatment ``_async_call`` gives the play command
        that follows it.
        """
        try:
            resolved = await self._resolve_quick_link(folder)
        except DisklavierError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if resolved is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="quick_link_not_found",
                translation_placeholders={"folder": folder},
            )
        album, _ = resolved
        await self._async_call(
            self.coordinator.client.async_play_album(
                album.album_id, SongGroup.PC_SHARING_FOLDER
            )
        )

    def _song_nodes(self, group: SongGroup, songs: list[Song]) -> list[BrowseMedia]:
        """Build playable track nodes for the songs of one library or folder."""
        return [
            BrowseMedia(
                title=song.title,
                media_class=MediaClass.TRACK,
                media_content_type=MediaType.MUSIC,
                media_content_id=f"{CONTENT_SONG}/{group.value}/{song.song_id}",
                can_play=True,
                can_expand=False,
            )
            for song in songs
        ]

    async def _browse_playlist_library(self, group: PlaylistGroup) -> BrowseMedia:
        """List the playlists in one library."""
        playlists = await self.coordinator.client.async_get_playlists(group)

        return BrowseMedia(
            title=dict(_PLAYLIST_LIBRARIES).get(group, group.value),
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            media_content_id=f"playlists/{group.value}",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.PLAYLIST,
            children=[
                BrowseMedia(
                    title=playlist.title,
                    media_class=MediaClass.PLAYLIST,
                    media_content_type=MediaType.PLAYLIST,
                    media_content_id=(
                        f"{CONTENT_PLAYLIST}/{group.value}/{playlist.playlist_id}"
                    ),
                    # Opens in one tap; the playlist's page plays it. See
                    # _album_level_nodes.
                    can_play=False,
                    can_expand=True,
                )
                for playlist in playlists
            ],
        )

    async def _browse_playlist(
        self, group: PlaylistGroup, playlist_id: int
    ) -> BrowseMedia:
        """List the songs inside one playlist."""
        items = await self.coordinator.client.async_get_playlist_items(
            playlist_id, group
        )

        return BrowseMedia(
            title="Playlist",
            media_class=MediaClass.PLAYLIST,
            media_content_type=MediaType.PLAYLIST,
            media_content_id=f"{CONTENT_PLAYLIST}/{group.value}/{playlist_id}",
            can_play=True,
            can_expand=True,
            children_media_class=MediaClass.TRACK,
            children=[
                BrowseMedia(
                    title=item.title,
                    media_class=MediaClass.TRACK,
                    media_content_type=MediaType.MUSIC,
                    media_content_id=(
                        f"{CONTENT_PLAYLIST_ITEM}/{group.value}/{item.song_id}"
                    ),
                    can_play=True,
                    can_expand=False,
                )
                for item in items
            ],
        )

    def _browse_random(self) -> BrowseMedia:
        """List the Surprise Me nodes: one random pick per built-in genre.

        The randomness is the piano's own -- ``select=random`` in the firmware -- so
        this browse level needs no request at all.
        """
        return BrowseMedia(
            title="Surprise Me",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            media_content_id=CONTENT_RANDOM,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.TRACK,
            children=[
                BrowseMedia(
                    title=title,
                    media_class=MediaClass.TRACK,
                    media_content_type=MediaType.MUSIC,
                    media_content_id=f"{CONTENT_RANDOM}/{genre.value}",
                    can_play=True,
                    can_expand=False,
                )
                for genre, title in _RANDOM_GENRES
            ],
        )

    # ------------------------------------------------------------------
    # Searching
    # ------------------------------------------------------------------

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search the piano's libraries, playlists and radio channels by title.

        The ranking runs in aiodisklavier over the piano's own song database, so one
        fetch covers every library and each result plays by exact id -- unlike the
        firmware's ``search_title``, which plays its single fuzzy pick sight unseen.
        """
        try:
            results = await self.coordinator.client.async_search(query.search_query)
        except DisklavierError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="browse_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        items: list[BrowseMedia] = []
        for result in results:
            if (
                result.kind is SearchKind.SONG
                and result.song is not None
                and result.song.group is not None
            ):
                items.append(
                    BrowseMedia(
                        title=result.title,
                        media_class=MediaClass.TRACK,
                        media_content_type=MediaType.MUSIC,
                        media_content_id=(
                            f"{CONTENT_SONG}/{result.song.group.value}"
                            f"/{result.song.song_id}"
                        ),
                        can_play=True,
                        can_expand=False,
                    )
                )
            elif (
                result.kind is SearchKind.PLAYLIST
                and result.playlist is not None
                and result.playlist_group is not None
            ):
                items.append(
                    BrowseMedia(
                        title=result.title,
                        media_class=MediaClass.PLAYLIST,
                        media_content_type=MediaType.PLAYLIST,
                        media_content_id=(
                            f"{CONTENT_PLAYLIST}/{result.playlist_group.value}"
                            f"/{result.playlist.playlist_id}"
                        ),
                        # Opens in one tap; the playlist's page plays it. See
                        # _album_level_nodes.
                        can_play=False,
                        can_expand=True,
                    )
                )
            elif result.kind is SearchKind.RADIO and result.channel is not None:
                items.append(
                    BrowseMedia(
                        title=result.title,
                        media_class=MediaClass.CHANNEL,
                        media_content_type=MediaType.CHANNEL,
                        media_content_id=(
                            f"{CONTENT_RADIO}/{result.channel.channel_id}"
                        ),
                        can_play=True,
                        can_expand=False,
                    )
                )
        return SearchMedia(result=items)

    async def _browse_radio(self) -> BrowseMedia:
        """List the radio channels.

        Radio is unavailable in some regions, where this comes back as an empty list.
        """
        channels = await self.coordinator.client.async_get_radio_channels()

        return BrowseMedia(
            title="Radio",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            media_content_id=CONTENT_RADIO,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.CHANNEL,
            children=[
                BrowseMedia(
                    title=channel.title,
                    media_class=MediaClass.CHANNEL,
                    media_content_type=MediaType.CHANNEL,
                    media_content_id=f"{CONTENT_RADIO}/{channel.channel_id}",
                    can_play=True,
                    can_expand=False,
                )
                for channel in channels
            ],
        )
