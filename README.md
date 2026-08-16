# Yamaha Disklavier for Home Assistant

Local control of a **Yamaha Disklavier ENSPIRE** from Home Assistant. Everything runs over
your own network — no cloud service, no account, no Yamaha servers.

Built on [aiodisklavier](https://github.com/reubenbijl/aiodisklavier), which carries the
protocol work and its [full reference](https://github.com/reubenbijl/aiodisklavier/blob/main/docs/enspire-api.md).

Verified against firmware **5.24.00** on a Disklavier ENSPIRE PRO grand.

## What you can do with it

- **Play the piano from an automation** — a wake-up piece in the morning, something quiet in
  the evening, a specific song when someone comes home.
- **Use it as a doorbell or notification**, either through the media player or through
  `aiodisklavier`'s one-shot notify helper, which restores whatever was playing afterwards.
- **Silence it on a schedule** — switch to quiet mode after bedtime so the keys still move
  but the hammers do not strike.
- **Browse and play the whole library** from the Home Assistant media browser, with each
  library's folder structure intact: the built-in songs, My Songs, your recordings,
  downloaded songs, the PC sharing folder, playlists, the demo playlist, and radio.
- **See what it is playing** on a dashboard, with title, artist, position and duration.

## Supported devices

Disklavier **ENSPIRE** models that expose the local HTTP API — confirmed on an ENSPIRE PRO
grand running 5.24.00. ENSPIRE ST and CL should work identically; reports welcome.

**Not supported:** Mark IV, E3 and earlier. Those speak a different protocol entirely. If
your piano offers a MusicCast integration instead, that is a different device family and
this integration will not find it.

## Installation

### HACS

Add this repository as a custom repository (category: **Integration**), install it, and
restart Home Assistant.

### Manually

Copy `custom_components/disklavier/` into your Home Assistant `config/custom_components/`
directory and restart.

## Setting it up

The piano is discovered automatically over SSDP and appears under **Settings → Devices &
Services**. Accept the discovery and you are done.

To add it by hand instead, choose **Add Integration → Yamaha Disklavier** and enter its
address.

| Parameter | Description |
|---|---|
| **Host** | The piano's hostname or IP address on your network. Find it on the piano under **Settings → Network**. |

No password or account is needed — the local API is unauthenticated.

If the piano later moves to a different IP address, it is normally picked up automatically by
rediscovery. If it is not, use **Reconfigure** on the integration entry rather than deleting
and re-adding it, which would lose the entity history. Reconfigure refuses to point an entry
at a *different* piano.

### Removing it

Delete the integration entry from **Settings → Devices & Services**. Nothing is left behind
on the piano; the integration only ever reads and sends commands.

## Entities

| Entity | Platform | What it does |
|---|---|---|
| Disklavier | `media_player` | Play, pause, stop, next, previous, seek, volume, repeat and shuffle, power, search, and the full media browser |
| Quiet mode | `select` | **Acoustic** or **Quiet** — whether the hammers physically strike the strings |
| Song type | `sensor` | What the loaded song is: **PianoSoft solo**, **PianoSoft Plus**, **PianoSoft PlusAudio**, **MIDI file** or **Audio** — with an `audio_output` attribute saying whether playback uses the speakers. Trigger a receiver on it |
| Play test chord | `button` | Sounds a chord to confirm the piano is responding. Disabled by default, because pressing it makes a noise |

## Playing something specific

`media_player.play_media` accepts these `media_content_id` forms:

```
song/<group>/<id>              e.g. song/built_in_songs/1
album/<group>/<id>
playlist/<group>/<id>
playlist_item/<group>/<id>
radio/<channel_id>
random/<genre>                 e.g. random/jazz — the piano picks
search/<title>                 e.g. search/Clair de lune
```

The media browser also has a **search box** (and voice assistants can use the same
search): results come ranked from the piano's own song database, cover every library,
playlists and radio, and play by exact id. `search/<title>` remains for scripts — it is a
single fuzzy pick made by the piano itself, sight unseen.

`search/` is a fuzzy title match run on the piano itself, which makes it the practical choice
for voice assistants and scripts — you do not need to know any ids:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.disklavier_pro
data:
  media_content_type: music
  media_content_id: search/Clair de lune
```

A gentle wake-up, quiet enough not to startle anyone:

```yaml
automation:
  - alias: Morning piano
    triggers:
      - trigger: time
        at: "07:30:00"
    actions:
      - action: media_player.turn_on
        target:
          entity_id: media_player.disklavier_pro
      # Waking takes about twelve seconds, and commands sent during it are ignored.
      - delay: "00:00:15"
      - action: media_player.volume_set
        target:
          entity_id: media_player.disklavier_pro
        data:
          volume_level: 0.4
      - action: media_player.play_media
        target:
          entity_id: media_player.disklavier_pro
        data:
          media_content_type: music
          media_content_id: search/Clair de lune
```

Turn the receiver on when a PianoSoft song with audio starts:

```yaml
automation:
  - alias: Receiver follows the piano
    triggers:
      - trigger: state
        entity_id: media_player.disklavier_pro
        to: playing
    conditions:
      - condition: state
        entity_id: sensor.disklavier_pro_song_type
        state: ["plus", "plus_audio", "audio"]
    actions:
      - action: media_player.turn_on
        target:
          entity_id: media_player.receiver
      - action: media_player.select_source
        target:
          entity_id: media_player.receiver
        data:
          source: Piano
```

Silence the room after bedtime without stopping playback:

```yaml
      - action: select.select_option
        target:
          entity_id: select.disklavier_pro_quiet_mode
        data:
          option: quiet
```

## How data updates

The integration polls the piano every **5 seconds** over HTTP. The piano's own web interface
polls twice a second, so this is well within what it expects. Transport commands show their
effect immediately, and every command also forces a refresh about a second later — once the
firmware has caught up with itself — so the UI never waits out a full poll interval.

Playback position is interpolated between polls, so the progress bar moves smoothly rather
than stepping every five seconds.

## Known limitations

These are properties of the piano's firmware, not of the integration:

- **Turning it on takes about twelve seconds.** The piano reports a transitional waking
  state and ignores commands throughout. The entity stays *off* until it is genuinely ready,
  so add a delay before sending commands after `turn_on`.
- **Stop and pause look the same.** The firmware has no stop state — stopping reports as
  paused at position zero, which appears as *idle*.
- **Radio swallows transport commands.** While a radio channel is playing, play and pause
  may appear to succeed without doing anything.
- **Repeat and shuffle are one setting on the piano.** Home Assistant shows them as two
  controls; turning shuffle on implies repeat, because the piano cannot shuffle without it.
- **Recording is not exposed.** The API supports it, but it is untested here and not wired up.
- **A passcode-protected piano is untested.** If yours has one set, please open an issue.

## Troubleshooting

**The piano is not discovered.** SSDP discovery needs Home Assistant and the piano on the
same network segment, with multicast allowed between them. Add it by IP address instead —
**Add Integration → Yamaha Disklavier**.

**It shows as unavailable.** The integration could not reach the piano. Check the address is
still right, and that you can load `http://<piano-ip>/api/1.0/current_info` in a browser.
Note that the piano answers HTTP even in standby, so "unavailable" means a network problem
rather than the piano being asleep — an asleep piano shows as *off*.

**Commands do nothing.** Check whether the piano is waking (see above), or playing radio.

**The media browser shows an error.** That means a library failed to list, rather than being
empty — an empty library shows as an empty folder. Check the piano is still reachable.

**Reporting a problem.** Download diagnostics from the integration's device page and attach
them to the issue. The piano's address and serial number are redacted automatically.

## Development

The test suite runs against a real Home Assistant, which needs Python 3.14. If you do not
have one, `.devtools/run` executes anything inside a container that does:

```bash
.devtools/run pytest
.devtools/run ruff check .
.devtools/run mypy custom_components/disklavier
```

It expects an `aiodisklavier` checkout alongside this one, and puts it on `PYTHONPATH` so
the integration is always tested against the library source.

Coverage is held at **100%**, and CI additionally runs Home Assistant's own `hassfest`
validator and the HACS action.

## Quality scale

This integration targets the **platinum** tier of Home Assistant's integration quality scale.
Every rule's status, including the ones that are exempt and why, is recorded in
[`quality_scale.yaml`](custom_components/disklavier/quality_scale.yaml).

One rule cannot be satisfied from this repository: `brands` requires a pull request to
[home-assistant/brands](https://github.com/home-assistant/brands). Source artwork is in
[`assets/`](assets).

## Licence

MIT
