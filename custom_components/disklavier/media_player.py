"""Media player entity for the Yamaha Disklavier integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aiodisklavier import (
    VOLUME_MAX,
    Album,
    DisklavierError,
    PlaylistGroup,
    PowerStatus,
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
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONTENT_ALBUM,
    CONTENT_PLAYLIST,
    CONTENT_PLAYLIST_ITEM,
    CONTENT_RADIO,
    CONTENT_SEARCH,
    CONTENT_SONG,
    DOMAIN,
    MS_PER_SECOND,
)
from .coordinator import DisklavierConfigEntry, DisklavierCoordinator
from .entity import DisklavierEntity

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
    )

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the media player."""
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.static_info.disklavier_id

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        """Return the player state.

        ``wakeup`` is reported as off: the piano is still ~12 seconds from accepting
        commands, so presenting it as on would invite failures.
        """
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

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._async_call(self.coordinator.client.async_pause())

    async def async_media_stop(self) -> None:
        """Stop playback and rewind."""
        await self._async_call(self.coordinator.client.async_stop())

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
            search/<title>
        """
        client = self.coordinator.client
        kind, _, rest = media_id.partition("/")

        try:
            if kind == CONTENT_SEARCH:
                await self._async_call(client.async_play_search(rest))
                return
            if kind == CONTENT_RADIO:
                await self._async_call(client.async_play_radio(int(rest)))
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
        whose remaining title holds no separator is a playable folder of songs, and
        every distinct leading segment becomes a virtual directory, kept in the
        piano's own ordering at first appearance.
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
                        can_play=True,
                        can_expand=True,
                    )
                )
        return nodes

    async def _browse_album(self, group: SongGroup, album_id: int) -> BrowseMedia:
        """List the songs inside one folder of a library."""
        albums = await self.coordinator.client.async_get_albums(group)
        songs = await self.coordinator.client.async_get_songs_in_album(album_id, group)
        title = next((a.title for a in albums if a.album_id == album_id), "")
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
                    can_play=True,
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
