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

#: Milliseconds per second, for converting the piano's positions to Home Assistant's seconds.
MS_PER_SECOND: Final = 1000

# media_content_id prefixes used by browse_media and play_media.
CONTENT_SONG: Final = "song"
CONTENT_ALBUM: Final = "album"
CONTENT_PLAYLIST: Final = "playlist"
CONTENT_PLAYLIST_ITEM: Final = "playlist_item"
CONTENT_RADIO: Final = "radio"
CONTENT_SEARCH: Final = "search"
CONTENT_RANDOM: Final = "random"
