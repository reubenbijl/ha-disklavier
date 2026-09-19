"""Constants for the Yamaha Disklavier integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "disklavier"

MANUFACTURER: Final = "Yamaha"

#: The piano's own web UI polls twice a second. That is far more than Home Assistant needs;
#: five seconds keeps the entity responsive while leaving the piano alone, and every command
#: requests an immediate refresh anyway.
SCAN_INTERVAL: Final = timedelta(seconds=5)

#: Waking from standby takes about twelve seconds, during which the piano ignores commands.
WAKEUP_SECONDS: Final = 15

#: How long the firmware keeps reporting the previous state after accepting a command.
#: Polling sooner only reads back the state the command just replaced.
COMMAND_SETTLE_SECONDS: Final = 1.0

#: How long after the piano reports a library change before Quick Links are refreshed.
#: Album listings can still fail with "no album" for a few seconds after a reindex, and a
#: refresh inside that window would leave a folder unresolved until someone opened it.
LIBRARY_SETTLE_SECONDS: Final = 5

#: Milliseconds per second, for converting the piano's positions to Home Assistant's seconds.
MS_PER_SECOND: Final = 1000

#: The hold_playback action, its fields, and the attribute a held song carries.
SERVICE_HOLD_PLAYBACK: Final = "hold_playback"
ATTR_DURATION: Final = "duration"
ATTR_MESSAGE: Final = "message"
ATTR_HOLD_UNTIL: Final = "hold_until"

#: The longest a song can be held.
HOLD_MAX: Final = timedelta(minutes=10)

#: How often the piano is read while a held song is still loading, so that it is stopped
#: within half a second of starting rather than up to a poll interval later.
HOLD_WATCH_INTERVAL: Final = timedelta(seconds=0.5)

#: How long that closer watch lasts. Loading takes a few seconds; one still loading after
#: this is left to the regular poll, which stops it just the same, only later.
HOLD_WATCH_LIMIT: Final = timedelta(seconds=30)

# media_content_id prefixes used by browse_media and play_media.
CONTENT_SONG: Final = "song"
CONTENT_ALBUM: Final = "album"
CONTENT_PLAYLIST: Final = "playlist"
CONTENT_PLAYLIST_ITEM: Final = "playlist_item"
CONTENT_RADIO: Final = "radio"
CONTENT_SEARCH: Final = "search"
CONTENT_RANDOM: Final = "random"
CONTENT_QUICK_LINKS: Final = "quick_links"
CONTENT_QUICK_LINK: Final = "quick_link"
CONTENT_LIBRARY: Final = "library"
CONTENT_ALBUM_DIR: Final = "album_dir"
CONTENT_PLAYLISTS: Final = "playlists"
