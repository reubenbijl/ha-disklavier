"""Sensor entities for the Yamaha Disklavier integration."""

from __future__ import annotations

from aiodisklavier import SongFormat
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DisklavierConfigEntry, DisklavierCoordinator
from .entity import DisklavierEntity

PARALLEL_UPDATES = 1

#: Piano media format -> sensor option. The names follow Yamaha's own catalogue
#: language: PianoSoft solo, PianoSoft Plus (XG-scored accompaniment), PianoSoft
#: PlusAudio (a recorded backing track). Plain MIDI files and bare audio round it out.
_FORMAT_OPTIONS: dict[SongFormat, str] = {
    SongFormat.SMF: "midi",
    SongFormat.SMF_SOLO: "solo",
    SongFormat.SMF_XG: "plus",
    SongFormat.SMF_WAV: "plus_audio",
    SongFormat.SMF_MP3: "plus_audio",
    SongFormat.WAV: "audio",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DisklavierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Disklavier sensors."""
    async_add_entities([DisklavierSongTypeSensor(entry.runtime_data)])


class DisklavierSongTypeSensor(DisklavierEntity, SensorEntity):
    """What kind of song is loaded, as the piano's own database classifies it.

    The point of this sensor is automation: a PianoSoft song with audio needs the
    speaker path switched on -- an amplifier or a receiver on the piano's OMNI OUT --
    where a solo song moves only the keys. Trigger on ``plus``, ``plus_audio`` or
    ``audio`` (or on the ``audio_output`` attribute) together with the media player
    playing, and the receiver takes care of itself.
    """

    _attr_translation_key = "song_type"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(set(_FORMAT_OPTIONS.values()))

    def __init__(self, coordinator: DisklavierCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.static_info.disklavier_id}_song_type"

    @property
    def native_value(self) -> str | None:
        """Return the loaded song's type, or None when there is nothing to classify.

        Unknown covers three honest cases: no song loaded, the piano's extended state
        unavailable, or a format this integration has never seen.
        """
        song = self.coordinator.data.song
        if song is None or song.format is None:
            return None
        return _FORMAT_OPTIONS.get(song.format)

    @property
    def extra_state_attributes(self) -> dict[str, bool | None]:
        """Expose the library's speaker-path rule directly, for simple triggers."""
        song = self.coordinator.data.song
        return {"audio_output": None if song is None else song.has_audio}
