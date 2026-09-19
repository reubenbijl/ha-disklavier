"""Tests for holding a song until something else is ready for it."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from aiodisklavier import (
    CurrentInfo,
    DisklavierCommandError,
    MasterState,
    PlaybackStatus,
    PowerStatus,
)
from homeassistant.components.media_player import (
    ATTR_MEDIA_SEEK_POSITION,
)
from homeassistant.components.media_player import (
    DOMAIN as MP_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_SEEK,
    SERVICE_MEDIA_STOP,
    SERVICE_TURN_OFF,
    STATE_BUFFERING,
    STATE_PLAYING,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.disklavier.const import DOMAIN

from .conftest import on_the_radio

ENTITY = "media_player.disklavier_pro"
MESSAGE = "Waiting for the receiver"


def _playing(current_info: CurrentInfo) -> CurrentInfo:
    """Return the fixture's song as the piano reports it just after it starts."""
    return replace(current_info, playback_status=PlaybackStatus.PLAY, position_ms=0)


def _loading(current_info: CurrentInfo) -> CurrentInfo:
    """Return the fixture's song as the piano reports it while it loads."""
    return replace(current_info, playback_status=PlaybackStatus.LOAD, position_ms=0)


async def _piano_reports(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_client: AsyncMock,
    current: CurrentInfo,
    master: MasterState | None = None,
) -> None:
    """Have the piano report ``current`` from now on, and take one poll of it."""
    mock_client.async_get_current_info.return_value = current
    if master is not None:
        mock_client.async_get_master_state.return_value = master
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def _hold(
    hass: HomeAssistant, seconds: float = 20, message: str | None = MESSAGE
) -> None:
    """Call disklavier.hold_playback on the piano, the way an automation would."""
    data: dict[str, object] = {ATTR_ENTITY_ID: ENTITY, "duration": {"seconds": seconds}}
    if message is not None:
        data["message"] = message
    await hass.services.async_call(DOMAIN, "hold_playback", data, blocking=True)


async def _pass(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Let ``seconds`` go by, and everything due in them run."""
    freezer.tick(seconds)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _command(hass: HomeAssistant, service: str, **data: object) -> None:
    """Call a media player command on the piano."""
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY, **data}, blocking=True
    )


async def test_a_playing_song_is_stopped_and_shown_waiting(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """The song is rewound, and the player says it is buffering, and why."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))

    await _hold(hass)

    mock_client.async_stop.assert_awaited_once()
    state = hass.states.get(ENTITY)
    assert state.state == STATE_BUFFERING
    assert state.attributes["media_artist"] == MESSAGE
    assert state.attributes["media_title"] == current_info.song_title
    held_until = state.attributes["hold_until"]
    assert abs(held_until - (dt_util.utcnow() + timedelta(seconds=20))) < timedelta(
        seconds=1
    )


async def test_the_song_plays_from_the_start_when_the_hold_runs_out(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """Once the time is up the song plays, and the player is the piano's own again."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass)
    # Stopped, the piano reports a paused song at its start.
    mock_client.async_get_current_info.return_value = replace(
        current_info, position_ms=0
    )

    await _pass(hass, freezer, 19)
    mock_client.async_play.assert_not_awaited()
    assert hass.states.get(ENTITY).state == STATE_BUFFERING

    await _pass(hass, freezer, 2)
    mock_client.async_play.assert_awaited_once()
    state = hass.states.get(ENTITY)
    assert state.state == STATE_PLAYING
    assert state.attributes["media_artist"] == current_info.song_artist
    assert "hold_until" not in state.attributes


async def test_play_lets_a_held_song_go_at_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """Pressing play is how whatever the song waits for says it is ready."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass)

    await _command(hass, SERVICE_MEDIA_PLAY)
    mock_client.async_play.assert_awaited_once()
    assert hass.states.get(ENTITY).state == STATE_PLAYING

    await _pass(hass, freezer, 30)
    mock_client.async_play.assert_awaited_once()


async def test_play_goes_straight_from_waiting_to_playing(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """No glimpse of the stopped song between the hold and the play.

    A state written in between would read idle, and set off anything that triggers
    on the piano stopping.
    """
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass)
    # Stopped by the hold, the piano reports its song paused at the start.
    await _piano_reports(
        hass, init_integration, mock_client, replace(current_info, position_ms=0)
    )
    assert hass.states.get(ENTITY).state == STATE_BUFFERING
    states: list[str] = []

    @callback
    def record(event: Event[EventStateChangedData]) -> None:
        if (new_state := event.data["new_state"]) is not None:
            states.append(new_state.state)

    async_track_state_change_event(hass, [ENTITY], record)
    await _command(hass, SERVICE_MEDIA_PLAY)
    await hass.async_block_till_done()

    assert states == [STATE_PLAYING]


async def test_a_refused_play_still_ends_the_hold(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """The caller hears of it, and the player no longer claims the song is waiting."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass)
    mock_client.async_play.side_effect = DisklavierCommandError("busy")

    with pytest.raises(HomeAssistantError):
        await _command(hass, SERVICE_MEDIA_PLAY)

    assert "hold_until" not in hass.states.get(ENTITY).attributes


@pytest.mark.parametrize(
    ("service", "data", "command"),
    [
        (SERVICE_MEDIA_PAUSE, {}, "async_pause"),
        (SERVICE_MEDIA_STOP, {}, "async_stop"),
        (SERVICE_MEDIA_NEXT_TRACK, {}, "async_next_song"),
        (SERVICE_MEDIA_PREVIOUS_TRACK, {}, "async_previous_song"),
        (SERVICE_MEDIA_SEEK, {ATTR_MEDIA_SEEK_POSITION: 30}, "async_seek"),
        (SERVICE_TURN_OFF, {}, "async_turn_off"),
    ],
)
async def test_other_commands_end_the_hold(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
    service: str,
    data: dict[str, object],
    command: str,
) -> None:
    """Pause, stop, skipping, seeking and power all mean the song is not wanted now."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass)
    getattr(mock_client, command).reset_mock()

    await _command(hass, service, **data)

    getattr(mock_client, command).assert_awaited_once()
    assert "hold_until" not in hass.states.get(ENTITY).attributes
    await _pass(hass, freezer, 30)
    mock_client.async_play.assert_not_awaited()


async def test_another_song_ends_the_hold(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """Something else was chosen, so it plays instead."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass)

    await hass.services.async_call(
        MP_DOMAIN,
        "play_media",
        {
            ATTR_ENTITY_ID: ENTITY,
            "media_content_type": "music",
            "media_content_id": "song/built_in_songs/1",
        },
        blocking=True,
    )

    assert "hold_until" not in hass.states.get(ENTITY).attributes
    await _pass(hass, freezer, 30)
    mock_client.async_play.assert_not_awaited()


async def test_a_loading_song_is_stopped_the_moment_it_starts(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """The hold can be placed while the song loads; the piano is watched closely."""
    await _piano_reports(hass, init_integration, mock_client, _loading(current_info))

    await _hold(hass)
    mock_client.async_stop.assert_not_awaited()
    assert hass.states.get(ENTITY).state == STATE_BUFFERING

    # Still loading at the next close look: nothing to stop yet.
    await _pass(hass, freezer, 0.6)
    mock_client.async_stop.assert_not_awaited()

    mock_client.async_get_current_info.return_value = _playing(current_info)
    await _pass(hass, freezer, 0.6)
    mock_client.async_stop.assert_awaited_once()
    assert hass.states.get(ENTITY).state == STATE_BUFFERING

    # Further polls, the piano not yet caught up, send no second stop.
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    mock_client.async_stop.assert_awaited_once()

    await _pass(hass, freezer, 20)
    mock_client.async_play.assert_awaited_once()


async def test_a_song_still_loading_when_the_hold_ends_is_left_to_start(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """A song never stopped needs no play: it starts by itself once loaded."""
    await _piano_reports(hass, init_integration, mock_client, _loading(current_info))
    await _hold(hass, seconds=1)

    await _pass(hass, freezer, 1.5)

    mock_client.async_play.assert_not_awaited()
    state = hass.states.get(ENTITY)
    assert state.state == STATE_BUFFERING  # the piano's own loading, now
    assert "hold_until" not in state.attributes


async def test_play_during_a_hold_on_a_loading_song_sends_nothing(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """Released before it started, the song starts by itself once it has loaded."""
    await _piano_reports(hass, init_integration, mock_client, _loading(current_info))
    await _hold(hass)

    await _command(hass, SERVICE_MEDIA_PLAY)

    mock_client.async_play.assert_not_awaited()
    assert "hold_until" not in hass.states.get(ENTITY).attributes


async def test_the_close_watch_gives_way_to_the_regular_poll(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """A song that is still loading after half a minute is left to the 5 s poll."""
    await _piano_reports(hass, init_integration, mock_client, _loading(current_info))
    await _hold(hass, seconds=300)

    for _ in range(62):
        await _pass(hass, freezer, 0.5)

    polls = mock_client.async_get_current_info.await_count
    for _ in range(8):
        await _pass(hass, freezer, 0.5)
    assert mock_client.async_get_current_info.await_count - polls <= 1

    # The regular poll still stops the song when it does start.
    mock_client.async_get_current_info.return_value = _playing(current_info)
    await _pass(hass, freezer, 5)
    mock_client.async_stop.assert_awaited_once()


async def test_nothing_to_hold_is_refused(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """A paused song is not starting or playing, so there is nothing to hold."""
    with pytest.raises(ServiceValidationError) as err:
        await _hold(hass)
    assert err.value.translation_key == "hold_needs_a_song"
    mock_client.async_stop.assert_not_awaited()


async def test_the_radio_cannot_be_held(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    master_state: MasterState,
) -> None:
    """The piano ignores stop during the radio, so a hold is refused with the reason."""
    current, master = on_the_radio(current_info, master_state)
    await _piano_reports(hass, init_integration, mock_client, current, master)

    with pytest.raises(ServiceValidationError) as err:
        await _hold(hass)
    assert err.value.translation_key == "radio_active"


async def test_holding_again_restarts_the_countdown(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """A second hold moves the end and replaces the message, without a second stop."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass, seconds=20)

    await _pass(hass, freezer, 10)
    await _hold(hass, seconds=20, message="Almost there")
    assert hass.states.get(ENTITY).attributes["media_artist"] == "Almost there"
    mock_client.async_stop.assert_awaited_once()

    await _pass(hass, freezer, 15)
    mock_client.async_play.assert_not_awaited()
    await _pass(hass, freezer, 6)
    mock_client.async_play.assert_awaited_once()


async def test_holding_a_loading_song_again_keeps_watching(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """A second hold on a song still loading keeps the one close watch going."""
    await _piano_reports(hass, init_integration, mock_client, _loading(current_info))
    await _hold(hass)
    await _hold(hass)

    mock_client.async_get_current_info.return_value = _playing(current_info)
    await _pass(hass, freezer, 0.6)
    mock_client.async_stop.assert_awaited_once()


async def test_a_hold_without_a_message_keeps_the_artist(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """The line under the title only changes when there is something to say."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))

    await _hold(hass, message=None)

    state = hass.states.get(ENTITY)
    assert state.state == STATE_BUFFERING
    assert state.attributes["media_artist"] == current_info.song_artist


@pytest.mark.parametrize("moved_on", ["asleep", "radio", "another_song"])
async def test_the_hold_ends_when_the_piano_moves_on(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    master_state: MasterState,
    freezer,
    moved_on: str,
) -> None:
    """Sleep, the radio or a song chosen at the piano leave nothing to hold."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass)

    if moved_on == "asleep":
        await _piano_reports(
            hass,
            init_integration,
            mock_client,
            replace(current_info, power_status=PowerStatus.SLEEP),
        )
    elif moved_on == "radio":
        current, master = on_the_radio(current_info, master_state)
        await _piano_reports(hass, init_integration, mock_client, current, master)
    else:
        await _piano_reports(
            hass,
            init_integration,
            mock_client,
            replace(_playing(current_info), song_title="Clair de lune"),
        )

    state = hass.states.get(ENTITY)
    assert state.state != STATE_BUFFERING
    assert "hold_until" not in state.attributes
    await _pass(hass, freezer, 30)
    mock_client.async_play.assert_not_awaited()


async def test_a_stop_the_piano_refuses_leaves_no_hold(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
) -> None:
    """The caller hears of it, and the player goes on showing the song playing."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    mock_client.async_stop.side_effect = DisklavierCommandError("busy")

    with pytest.raises(HomeAssistantError) as err:
        await _hold(hass)

    assert err.value.translation_key == "command_failed"
    state = hass.states.get(ENTITY)
    assert state.state == STATE_PLAYING
    assert "hold_until" not in state.attributes


async def test_a_loading_song_that_cannot_be_stopped_plays(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With nobody to tell, the failure is logged and the song is let play."""
    await _piano_reports(hass, init_integration, mock_client, _loading(current_info))
    await _hold(hass)
    mock_client.async_stop.side_effect = DisklavierCommandError("busy")

    mock_client.async_get_current_info.return_value = _playing(current_info)
    await _pass(hass, freezer, 0.6)

    assert "Could not stop the held song" in caplog.text
    state = hass.states.get(ENTITY)
    assert state.state == STATE_PLAYING
    assert "hold_until" not in state.attributes


async def test_a_failed_stop_after_the_hold_moved_on_is_only_logged(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stop that fails after play was pressed leaves the new state alone."""
    await _piano_reports(hass, init_integration, mock_client, _loading(current_info))
    await _hold(hass)

    async def stop_after_play() -> None:
        # The hold has been let go by the time the piano answers.
        await _command(hass, SERVICE_MEDIA_PLAY)
        raise DisklavierCommandError("busy")

    mock_client.async_stop.side_effect = stop_after_play
    mock_client.async_get_current_info.return_value = _playing(current_info)
    await _pass(hass, freezer, 0.6)

    assert "Could not stop the held song" in caplog.text
    assert "hold_until" not in hass.states.get(ENTITY).attributes


async def test_a_failed_release_is_logged(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A play the piano refuses when the time is up is reported, not raised."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))
    await _hold(hass)
    mock_client.async_play.side_effect = DisklavierCommandError("busy")

    await _pass(hass, freezer, 21)

    assert "Could not play the held song" in caplog.text
    assert "hold_until" not in hass.states.get(ENTITY).attributes


@pytest.mark.parametrize("seconds", [0, 601])
async def test_the_duration_is_bounded(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    seconds: int,
) -> None:
    """At least a second, at most ten minutes."""
    await _piano_reports(hass, init_integration, mock_client, _playing(current_info))

    with pytest.raises(vol.Invalid):
        await _hold(hass, seconds=seconds)
    mock_client.async_stop.assert_not_awaited()


async def test_unloading_drops_a_hold(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    current_info: CurrentInfo,
    freezer,
) -> None:
    """Nothing fires for a piano that has gone away."""
    await _piano_reports(hass, init_integration, mock_client, _loading(current_info))
    await _hold(hass)

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    await _pass(hass, freezer, 30)

    mock_client.async_play.assert_not_awaited()
    mock_client.async_stop.assert_not_awaited()
