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

The integration keeps the `hombee_air` domain so existing Hombee Air config
entries continue to load after upgrading.

## Installation

1. Install HACS in Home Assistant.
2. Open HACS, choose **Custom repositories**, and add this repository as an
   **Integration** repository.
3. Install **Hombee** from HACS.
4. Restart Home Assistant.
5. Open **Settings > Devices & services > Add integration** and search for
   **Hombee**.
6. Choose **Enable Hombee managed lighting** or **Connect a Hombee Air unit**.

The Hombee app configures managed lights through the admin-only WebSocket
commands `hombee_air/managed_lights/reconcile` and
`hombee_air/managed_lights/list`. The reconcile command accepts a complete,
revisioned manifest and can safely be repeated.

## Updates

HACS tracks GitHub Releases from this repository. Users install the integration
once, then receive updates through the normal HACS update flow whenever a new
release tag is published.

The release workflow uses conventional commits to decide the next version,
updates `custom_components/hombee_air/manifest.json`, creates the GitHub
Release, and lets HACS distribute the updated integration archive.

## Development

The development environment requires Python 3.14.2 or newer to run the Home
Assistant test suite. Direct dependencies use minimum versions without exact
pins or upper bounds. uv excludes prereleases and overrides the Home Assistant
test plugin's pytest and pytest-asyncio pins so those runners can stay current.
Upstream requirements still govern other transitive dependencies.

Dependabot checks daily and groups updates for each ecosystem. Lockfiles record
the versions tested by CI. Scheduled CI also refreshes dependencies before
testing, while PR and release builds use the committed lockfiles. Node tracks
the latest stable release. To refresh the lockfiles locally:

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
