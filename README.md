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

HACS tracks GitHub Releases from this repository. Users install the integration
once, then receive updates through the normal HACS update flow whenever a new
release tag is published.

The release workflow uses conventional commits to decide the next version,
updates `custom_components/hombee/manifest.json`, creates the GitHub
Release, and lets HACS distribute the updated integration archive.

## Development

The development environment requires Python 3.14.2 or newer to run the Home
Assistant test suite. Direct Python dependencies use minimum versions without exact
pins or upper bounds. uv excludes prereleases and overrides the Home Assistant
test plugin's pytest and pytest-asyncio pins so those runners can stay current.
Upstream requirements still govern other transitive dependencies. Home
Assistant also constrains pymodbus at runtime, so the manifest allows its
required version while the development lockfile tests the latest stable release.

All direct Bun dependencies use caret ranges, allowing minor and patch updates
within their current major versions. The conventional commits preset stays on
9.x until the release-notes generator supports conventional-changelog-writer 9
or newer. Upgrade the generator and preset together, then verify release-note
generation before merging.

Dependabot checks daily and groups updates for each ecosystem. All CI runs,
including scheduled runs, install Python and npm dependencies from the committed
lockfiles without updating them. Dependency updates arrive through Dependabot
PRs. Node tracks the latest stable release. To refresh the lockfiles locally:

```bash
uv lock --upgrade
bun update
```

```bash
uv sync --python 3.14 --extra dev
uv run ruff check .
uv run black --check .
uv run pytest
```

The CI pipeline also validates the repository with HACS, Hassfest, security
audits, and a Home Assistant Docker smoke test.
