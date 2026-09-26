# Hombee for Home Assistant

[![Continuous integration](https://github.com/hombee/hacs/actions/workflows/continuous-integration.yaml/badge.svg)](https://github.com/hombee/hacs/actions/workflows/continuous-integration.yaml)
[![Continuous delivery](https://github.com/hombee/hacs/actions/workflows/continuous-delivery.yaml/badge.svg)](https://github.com/hombee/hacs/actions/workflows/continuous-delivery.yaml)

Home Assistant Community Store integrations maintained by Hombee.

## Features

- **Hombee Voice** adds cloud transcription, GPT-6 Luna conversation and
  AI-generated speech to native Home Assistant Assist pipelines. Requires
  Home Assistant 2026.9 or later and a Hombee Pro association.
- **Managed lighting** controls brightness for dimmable lamps and color
  temperature for lamps that support it. Both attributes are applied in the
  first physical `light.turn_on` call. Room profiles set daily targets and
  optional illuminance sensors adjust brightness to the measured light.
  Manual brightness and color changes remain independent and last until the
  lamp turns off. Active lights are checked once per minute.
- **Hombee Air** controls Hombee Air HVAC units over Modbus TCP.

The integration domain is `hombee`. This is a breaking domain change.
Before upgrading an installation with existing `hombee_air` managed lights,
use the old integration's `hombee_air/managed_lights/list` command to read its
revision, then send `hombee_air/managed_lights/reconcile` with that revision
and `lights: []`. This restores the original physical entity IDs. Removing
the old entry alone does not restore those IDs.
Then remove the old integration entries, update through HACS, restart HA,
and add Hombee again, including Air units. New installations and subsequent
light discovery do not use a reconciliation API.

## Installation

1. Install HACS in Home Assistant.
2. Open HACS, choose **Custom repositories**, and add this repository as an
   **Integration** repository.
3. Install **Hombee** from HACS.
4. Restart Home Assistant.
5. Open **Settings > Devices & services > Add integration** and search for
   **Hombee**.
6. Choose managed lighting, a Hombee Air unit, or Hombee Voice.

## Hombee Voice

Open **Pro configuration** in the Hombee app after subscribing. Associate a
connected Home Assistant instance using an administrator connection and a
reachable remote URL. Pro includes one instance; additional Home Assistant
packages increase the allowance. Hombee creates a **Hombee Voice** Assist
pipeline automatically. Select it on your Assist device or voice satellite.

Voice processing uses `gpt-transcribe`, `gpt-6-luna`, and `gpt-audio-1.5`.
English and Polish speech are supported. Each audio request is limited to
30 seconds. Requests send audio, conversation context, and the available
exposed-entity tools to Hombee's metered OpenAI gateway. Your OpenAI API key
is never stored in Home Assistant. Spoken replies are AI-generated.

Each instance has a US$2 API allowance per UTC calendar month. The gateway
reserves the maximum cost before making a model request; interrupted requests
may keep that reservation. Cloud stages stop when there is insufficient budget.
Usage resets on the first day of the next month, and removing or reconnecting
an instance does not reset it. Local Assist intents remain available.

Cancelling renewal preserves paid access until expiry. Expired Pro access
disables cloud voice; expired additional packages pause assignments beyond the
remaining allowance, in association order. Remove an association in Pro
configuration to select another instance. The app explains missing or outdated
HACS installations and offers refresh after installation or upgrade.

After enabling managed lighting, Hombee automatically discovers registered
dimmable lights at startup and when new lights appear. Light groups
are excluded to avoid wrapping both a group and its members. No reconciliation
API call is needed.

Use `switch.hombee_circadian_lighting` to control circadian lighting throughout
the home through the UI or the standard `switch.turn_on` and `switch.turn_off`
service API. The setting survives restarts. Disabling it preserves ordinary
light control and stops automatic temperature writes. Enabling it clears manual
color overrides and updates currently active lights. The `sun` integration must be
enabled for the daytime curve; without solar data the warm temperature is used.

Brightness has its own `switch.hombee_adaptive_brightness`. Open the managed
lighting entry's **Configure** menu to change the default daily profile,
assign a room profile and lux sensor, or set an individual lamp's automatic
brightness limits. Room activity selectors support reading, cooking, relaxation,
night lighting, and the daily schedule. All settings survive restarts.

The initial brightness schedule uses 07:00 wake time, 23:00 sleep time, a
30-minute morning ramp, and a two-hour evening ramp. It starts with 100% day,
40% relaxation, and 5% night brightness. Adjust these values to your rooms and
sleep schedule. Existing managed-light installations gain automatic brightness
when upgrading; turn off the brightness switch to retain manual brightness.

See [the lighting setup guide](docs/lighting.md) for sensor placement, room
configuration, automation examples, manual control, and troubleshooting.

## Updates

Install updates through HACS when a new GitHub Release is available.

## Development

Install dependencies and run the checks:

```bash
uv sync --locked --extra dev
bun install --frozen-lockfile
uv run --locked ruff check .
uv run --locked black --check .
uv run --locked pytest
```

To update dependencies:

```bash
uv lock --upgrade
bun update
```
