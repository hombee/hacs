"""WebSocket contract for Hombee managed lighting."""

from types import SimpleNamespace
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee_air.const import (
    CONF_ENTRY_KIND,
    DOMAIN,
    ENTRY_KIND_MANAGED_LIGHTING,
    MANAGED_LIGHTING_UNIQUE_ID,
    WS_LIST_MANAGED_LIGHTS,
    WS_RECONCILE_MANAGED_LIGHTS,
)
from custom_components.hombee_air.websocket import (
    websocket_list_managed_lights,
    websocket_reconcile_managed_lights,
)


class _AdminConnection:
    user = SimpleNamespace(is_admin=True)

    def __init__(self) -> None:
        self.result: Any = None

    def send_result(self, _message_id: int, result: Any) -> None:
        self.result = result

    def send_error(self, _message_id: int, code: str, message: str) -> None:
        raise AssertionError(f"Unexpected WebSocket error {code}: {message}")


async def test_admin_can_reconcile_and_read_empty_manifest(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MANAGED_LIGHTING_UNIQUE_ID,
        title="Hombee managed lighting",
        data={CONF_ENTRY_KIND: ENTRY_KIND_MANAGED_LIGHTING},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    connection = _AdminConnection()
    websocket_reconcile_managed_lights(
        hass,
        connection,
        {
            "id": 1,
            "type": WS_RECONCILE_MANAGED_LIGHTS,
            "revision": 7,
            "lights": [],
        },
    )
    await hass.async_block_till_done()
    assert connection.result == {"revision": 7, "lights": []}

    websocket_list_managed_lights(
        hass,
        connection,
        {"id": 2, "type": WS_LIST_MANAGED_LIGHTS},
    )
    assert connection.result == {"revision": 7, "lights": []}

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
