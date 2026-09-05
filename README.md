# Hombee for Home Assistant

[![Continuous integration](https://github.com/hombee/hacs/actions/workflows/continuous-integration.yaml/badge.svg)](https://github.com/hombee/hacs/actions/workflows/continuous-integration.yaml)
[![Continuous delivery](https://github.com/hombee/hacs/actions/workflows/continuous-delivery.yaml/badge.svg)](https://github.com/hombee/hacs/actions/workflows/continuous-delivery.yaml)

Home Assistant Community Store integrations maintained by Hombee.

## Features

- **Managed lighting** creates a public logical light for a physical
  color-temperature light. The logical light applies the current Hombee
  temperature in the first physical `light.turn_on` call, so the source does
  not briefly restore an old color. It keeps manual color changes until the
  light turns off and reconciles active lights once per minute.
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
6. Choose **Enable Hombee managed lighting** or **Connect a Hombee Air unit**.

After enabling managed lighting, Hombee automatically discovers registered
color-temperature lights at startup and when new lights appear. Light groups
are excluded to avoid wrapping both a group and its members. No reconciliation
API call is needed.

Use `switch.hombee_circadian_lighting` to control circadian lighting throughout
the home through the UI or the standard `switch.turn_on` and `switch.turn_off`
service API. The setting survives restarts. Disabling it preserves ordinary
light control and stops automatic temperature writes. Enabling it clears manual
overrides and updates currently active lights. The `sun` integration must be
enabled for the daytime curve; without solar data the warm temperature is used.

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
