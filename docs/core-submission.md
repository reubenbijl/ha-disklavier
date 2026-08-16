# Moving this integration into Home Assistant core

Notes for turning this custom component into a `home-assistant/core` pull request. The
integration code itself needs little change; most of the work is process and tests.

## Prerequisite: the library must be on PyPI

Core installs integration dependencies from PyPI and will not accept a git dependency. So
[aiodisklavier](https://github.com/reubenbijl/aiodisklavier) has to be published and the
version in `manifest.json` pinned exactly (`aiodisklavier==0.1.0`, not `>=`).

The library must not import Home Assistant — it does not, and it should stay that way. This
separation is the reason the two repositories exist.

## Changes to the integration itself

| Change | Why |
|---|---|
| Move to `homeassistant/components/disklavier/` | Core layout |
| **Remove `"version"` from `manifest.json`** | HACS requires it; core rejects it |
| `"documentation"` → `https://www.home-assistant.io/integrations/disklavier` | Core convention |
| Remove `"issue_tracker"` | Core issues go to the core tracker |
| Add `"quality_scale"` back, plus a `quality_scale.yaml` | Only once its rules are genuinely met |
| Check whether `translations/en.json` should be committed | `strings.json` is the source of truth and core's tooling generates and syncs translations; do not hand-maintain both |

## Tests

This is the real work, and the gating item. Core requires tests under
`tests/components/disklavier/`, and **the config flow must reach 100% coverage** — that rule
is enforced, not aspirational.

At minimum:

- `test_config_flow.py` — the user step (success, `cannot_connect`, `invalid_response`,
  unexpected error), the SSDP discovery step, and `already_configured` when the same
  `disklavier_id` is added twice
- `test_init.py` — setup, `ConfigEntryNotReady` when the piano is unreachable, unload
- `test_media_player.py` — state mapping, especially the three cases that are easy to get
  wrong: `wakeup` reporting as off, `stop` reporting as idle rather than paused, and the
  repeat/shuffle fold
- `snapshot` tests for the entity registry, using `syrupy`

Use `pytest-homeassistant-custom-component` locally, or work directly in a core checkout.
Mock at the `aiodisklavier.Disklavier` boundary rather than at HTTP — the library already has
its own HTTP-level tests, so repeating them here buys nothing.

## Also needed, outside the code PR

1. **Brands** — an icon PR to [home-assistant/brands](https://github.com/home-assistant/brands),
   adding `custom_integrations/disklavier/`. `assets/brands/` holds the files ready to copy:
   `icon.png` at 256x256 and `icon@2x.png` at 512x512, both square, exported from
   `assets/Disklavier Logo.pxd` via `assets/Disklavier Logo.png`. A `logo.png` is optional and
   is a different asset — landscape, at the wordmark's own proportions, shortest side 128-256
   (256-512 for `@2x`); compare `core_integrations/yamaha_musiccast/logo.png`, which is the
   product wordmark alone with no manufacturer mark.
2. **Documentation** — a page for
   [home-assistant.io](https://github.com/home-assistant/home-assistant.io) under
   `source/_integrations/disklavier.markdown`. Core will not merge an integration without it.
3. **hassfest** — run core's `python -m script.hassfest` and fix anything it flags before
   opening the PR.

## Things reviewers are likely to raise

- **Polling interval.** Currently 5 seconds. Be ready to justify it; the piano's own UI polls
  twice a second, so 5s is already conservative, and every command requests an immediate
  refresh.
- **The `/ctrl/` fallbacks.** Seek, repeat and shuffle, and the extended state block use the
  piano's internal unversioned endpoints rather than its open API. That is a deliberate
  trade-off — the open API cannot do those things at all — and it belongs in the PR
  description rather than being discovered during review.
- **`master.json` being best-effort.** Repeat and shuffle silently disappear if that read
  fails, rather than failing the whole update. Worth stating explicitly.
