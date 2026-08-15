# Yamaha Disklavier for Home Assistant

Local control of a **Yamaha Disklavier ENSPIRE** from Home Assistant. Everything runs over
your own network — no cloud service, no account, no Yamaha servers.

Built on [aiodisklavier](https://github.com/reubenbijl/aiodisklavier), which carries the
protocol work and its [full reference](https://github.com/reubenbijl/aiodisklavier/blob/main/docs/enspire-api.md).

Verified against firmware **5.24.00** on a Disklavier ENSPIRE PRO grand.

> This integration is intended for submission to Home Assistant core. Until it lands there,
> this repository is how you install it.

## Install

### HACS

Add this repository as a custom repository (category: Integration), install, and restart
Home Assistant.

### Manually

Copy `custom_components/disklavier/` into your Home Assistant `config/custom_components/`
directory and restart.

## Set up

The piano is discovered automatically over SSDP and will appear under **Settings → Devices &
Services**. You can also add it by IP address; find that on the piano under Settings →
Network.

No password or account is needed. If your piano has a passcode enabled the behaviour is
untested — please open an issue.

## Entities

| Entity | Platform | Notes |
|---|---|---|
| Disklavier | `media_player` | Transport, volume, seek, repeat and shuffle, power, and full library browsing |
| Quiet mode | `select` | Acoustic or Quiet — whether the hammers physically strike the strings |
| Play test chord | `button` | Disabled by default. Plays a C major triad for one second without disturbing a loaded song |

Browsing covers the built-in library, your recordings, downloaded songs, the PC sharing
folder, playlists, and DisklavierRadio channels.

### Playing something specific

`media_player.play_media` accepts these `media_content_id` forms:

```
song/<group>/<id>              e.g. song/built_in_songs/1
album/<group>/<id>
playlist/<group>/<id>
playlist_item/<group>/<id>
radio/<channel_id>
search/<title>                 e.g. search/Clair de lune
```

`search/` is a fuzzy title match run on the piano itself, which makes it the practical
choice for voice assistants and scripts:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.disklavier_pro
data:
  media_content_type: music
  media_content_id: search/Clair de lune
```

## Things the piano does that may surprise you

- **Turning it "on" takes about twelve seconds.** The piano reports a transitional `wakeup`
  state and ignores commands throughout. The entity shows as off until it is genuinely ready.
- **Stop and pause look the same.** The firmware has no stop state — stopping reports as
  paused at position zero, which the integration surfaces as idle.
- **Radio silently swallows transport commands.** While a radio channel is playing, play and
  pause return success but do nothing. This is the firmware's behaviour, not the
  integration's.

## Status and contributing

Working and verified on hardware. Known gaps before this is ready for core:

- No test suite yet. Core requires config-flow tests at minimum for the bronze quality scale.
- `manifest.json` carries a `version` key, which HACS requires and core forbids — it must be
  dropped when the integration moves into core.
- Behaviour with a passcode-protected piano is untested.
- Remote Lesson and Remote Live modes are unimplemented; their state endpoints only exist
  while those modes are running.

Issues and pull requests welcome, particularly reports from other ENSPIRE models and
firmware versions.

## Licence

MIT
