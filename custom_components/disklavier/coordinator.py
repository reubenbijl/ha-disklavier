"""Data update coordinator for the Yamaha Disklavier integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from aiodisklavier import (
    CurrentInfo,
    Disklavier,
    DisklavierConnectionError,
    DisklavierEnvelopeError,
    DisklavierError,
    LibrarySong,
    MasterState,
    PowerStatus,
    RadioChannel,
    StaticInfo,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type DisklavierConfigEntry = ConfigEntry[DisklavierCoordinator]


@dataclass(frozen=True, slots=True)
class DisklavierData:
    """A single poll of the piano.

    ``master`` comes from the piano's internal endpoint and is ``None`` when that read
    fails. Only repeat and shuffle depend on it, so the rest of the integration keeps
    working without it.
    """

    current: CurrentInfo
    master: MasterState | None
    #: What the piano's own database says about the loaded song -- most usefully its
    #: media format. ``None`` when nothing is loaded, or when ``master`` is unavailable
    #: (the loaded song's identity only exists there).
    song: LibrarySong | None
    #: When this poll completed. The media player reports it as
    #: ``media_position_updated_at``, whenever the position or play state changed, so the
    #: UI can extrapolate playback position between polls instead of stepping it every
    #: five seconds.
    fetched_at: datetime


def radio_list_is_free(current: CurrentInfo) -> bool:
    """Whether the radio channel list can be read without costing the listener anything.

    Asking the piano for it looks like a read and is not: the firmware stops its
    sequencer to fetch the list, so a song that is playing falls silent and one that is
    paused part-way is rewound to the start. Only a piano that is on and already stopped
    has nothing to lose -- or one playing the radio itself, which carries on undisturbed.
    """
    return current.power_status is PowerStatus.ON and (
        current.is_stopped or current.is_radio
    )


class DisklavierCoordinator(DataUpdateCoordinator[DisklavierData]):
    """Poll a Disklavier for its current state."""

    config_entry: DisklavierConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: DisklavierConfigEntry,
        client: Disklavier,
        static_info: StaticInfo,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.static_info = static_info
        self._master_warned = False
        #: The library stamp the client's song database is known to be current for.
        #: See ``_async_follow_library``.
        self._library_seen: int | None = None
        #: DisklavierRadio's channels, once they have been read: ``None`` until then, and
        #: an empty list where the piano says the service is not available. Search and
        #: the media browser work from this and never from a fresh read, because a fresh
        #: read stops the music -- see ``radio_list_is_free``. It is kept for the life of
        #: the config entry; reload the integration to pick up a changed line-up.
        self.radio_channels: list[RadioChannel] | None = None
        self._radio_read: asyncio.Task[None] | None = None

    async def _async_update_data(self) -> DisklavierData:
        """Fetch the piano's current state."""
        try:
            current = await self.client.async_get_current_info()
        except DisklavierConnectionError as err:
            raise UpdateFailed(f"Could not reach the Disklavier: {err}") from err
        except DisklavierError as err:
            raise UpdateFailed(
                f"Unexpected response from the Disklavier: {err}"
            ) from err

        # Anchor position extrapolation to the moment the position was actually read,
        # not to whenever the follow-up fetches finish.
        fetched_at = dt_util.utcnow()

        self._read_radio_channels_when_free(current)

        master: MasterState | None = None
        try:
            master = await self.client.async_get_master_state()
        except DisklavierError as err:
            # Best-effort: the open API already gave us everything essential.
            if not self._master_warned:
                _LOGGER.debug(
                    "Extended state unavailable, repeat and shuffle will be hidden: %s",
                    err,
                )
                self._master_warned = True

        if master is not None and master.library_updated is not None:
            await self._async_follow_library(master.library_updated)

        song: LibrarySong | None = None
        if (
            master is not None
            and master.song_prefix is not None
            and master.song_id is not None
        ):
            try:
                song = await self.client.async_lookup_song(
                    master.song_prefix, master.song_id
                )
            except DisklavierError:
                # Best-effort for the same reason: the song database is the internal
                # endpoint tier, and everything except the song-type sensor works
                # without it.
                song = None

        return DisklavierData(
            current=current, master=master, song=song, fetched_at=fetched_at
        )

    async def _async_follow_library(self, stamp: int) -> None:
        """Re-read the song database when the piano reports its library changed.

        The client keeps the database cached and only re-reads it for a song it has
        never seen. A reindex that changes a song it already holds -- a backing track
        synced beside an indexed MIDI file turns it from MIDI to PianoSoft PlusAudio --
        or adds songs nobody has looked up yet would otherwise leave the song type
        sensor and search on the old library. The stamp is recorded only once the read
        succeeds, so a failed read is tried again on the next poll. The first stamp seen
        needs no read: the client fetches the database afresh on first use.
        """
        if self._library_seen is None:
            self._library_seen = stamp
            return
        if stamp == self._library_seen:
            return
        try:
            await self.client.async_get_song_db(refresh=True)
        except DisklavierError:
            return
        self._library_seen = stamp

    def _read_radio_channels_when_free(self, current: CurrentInfo) -> None:
        """Read the radio channel list in the background, the first time it is harmless.

        So that by the time anyone searches or opens the Radio page, the list is already
        here and nothing has to be interrupted to get it. In the background because the
        read takes three or four seconds, which a poll should not wait for.
        """
        if self.radio_channels is not None or not radio_list_is_free(current):
            return
        # Asked of the task itself rather than tracked with a flag the task clears.
        # Home Assistant starts tasks eagerly, so a read that fails before it ever
        # suspends has run to the end -- cleanup included -- by the time the handle is
        # assigned here, and a flag cleared that early would then be set for good.
        if self._radio_read is not None and not self._radio_read.done():
            return
        self._radio_read = self.config_entry.async_create_background_task(
            self.hass, self._async_read_radio_channels(), f"{DOMAIN} radio channels"
        )

    async def _async_read_radio_channels(self) -> None:
        """Do the background read, leaving a failure for a later poll to try again."""
        try:
            await self.async_read_radio_channels()
        except DisklavierError as err:
            _LOGGER.debug("Could not read the radio channels, will try again: %s", err)

    async def async_read_radio_channels(self) -> list[RadioChannel]:
        """Read the radio channel list now, and keep it.

        The caller has decided the interruption is acceptable -- see
        ``radio_list_is_free``. A piano that declines, as one in a region without
        DisklavierRadio does, has no channels; that is an answer, not a failure, and it
        is remembered so the question is not put again.
        """
        try:
            self.radio_channels = await self.client.async_get_radio_channels()
        except DisklavierEnvelopeError as err:
            _LOGGER.debug("DisklavierRadio is not available: %s", err)
            self.radio_channels = []
        return self.radio_channels
