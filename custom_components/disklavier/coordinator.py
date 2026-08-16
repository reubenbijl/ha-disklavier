"""Data update coordinator for the Yamaha Disklavier integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from aiodisklavier import (
    CurrentInfo,
    Disklavier,
    DisklavierConnectionError,
    DisklavierError,
    LibrarySong,
    MasterState,
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
    #: ``media_position_updated_at`` so the UI can extrapolate playback position between
    #: polls instead of stepping it every five seconds.
    fetched_at: datetime


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
