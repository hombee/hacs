# Hombee HACS

Home Assistant Community Store integrations maintained by Hombee.

## Integrations

- **Hombee Air** (`hombee_air`): a local-polling Home Assistant integration for
  Hombee Air HVAC units over Modbus TCP.

## Installation

1. Install HACS in Home Assistant.
2. Open HACS, choose **Custom repositories**, and add this repository as an
   **Integration** repository.
3. Install **Hombee Air** from HACS.
4. Restart Home Assistant.
5. Open **Settings > Devices & services > Add integration**, search for
   **Hombee Air**, and enter the unit host, name, installation id, and port.

## Updates

HACS tracks GitHub Releases from this repository. Users install the integration
once, then receive updates through the normal HACS update flow whenever a new
release tag is published.

The release workflow uses conventional commits to decide the next version,
updates `custom_components/hombee_air/manifest.json`, creates the GitHub
Release, and lets HACS distribute the updated integration archive.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ruff check .
black --check .
pytest
```

The CI pipeline also validates the repository with HACS, Hassfest, security
audits, and a Home Assistant Docker smoke test.
