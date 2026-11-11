"""Bound multi-step conversation transport by the reserved turn's expiry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.hombee.assist_client import HombeeAssistClient, _Turn


def _client(hass):
    return HombeeAssistClient(
        hass,
        SimpleNamespace(
            data={
                "token": "test-token",
                "gatewayUrl": "https://hombee.app",
                "instanceId": "a" * 32,
            }
        ),
    )


@pytest.mark.parametrize(
    ("stage", "remaining", "expected"),
    [
        ("conversation", 240, 240),
        ("conversation", 0.25, 0.25),
        ("speech", 240, 40),
        ("transcription", 240, 40),
        ("turn", 240, 40),
        ("finish", 240, 40),
    ],
)
async def test_transport_deadlines_follow_turn_lifetime_only_for_conversation(
    hass, stage, remaining, expected
):
    client = _client(hass)
    response = SimpleNamespace(status=200, json=AsyncMock(return_value={"ok": True}))
    session = MagicMock()
    session.post.return_value.__aenter__ = AsyncMock(return_value=response)
    with (
        patch("custom_components.hombee.assist_client.time", return_value=1000),
        patch(
            "custom_components.hombee.assist_client.async_get_clientsession",
            return_value=session,
        ),
    ):
        assert await client._send(
            stage, {}, "request-1", turn=_Turn("turn-1", expires_at=1000 + remaining)
        ) == {"ok": True}
    session.post.assert_called_once()
    timeout = session.post.call_args.kwargs["timeout"]
    assert timeout.total == expected
    if stage == "conversation":
        assert timeout.ceil_threshold == float("inf")


@pytest.mark.parametrize("remaining", [0, -1])
async def test_expired_conversation_never_opens_transport(hass, remaining):
    client = _client(hass)
    session = MagicMock()
    with (
        patch("custom_components.hombee.assist_client.time", return_value=1000),
        patch(
            "custom_components.hombee.assist_client.async_get_clientsession",
            return_value=session,
        ),
        pytest.raises(HomeAssistantError),
    ):
        await client._send(
            "conversation",
            {},
            "request-1",
            turn=_Turn("turn-1", expires_at=1000 + remaining),
        )
    session.post.assert_not_called()
    assert client.diagnostic["code"] == "turn_expired"


async def test_conversation_transport_timeout_releases_turn_without_retry(hass):
    client = _client(hass)
    session = MagicMock()
    session.post.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError)
    with (
        patch("custom_components.hombee.assist_client.time", return_value=1000),
        patch(
            "custom_components.hombee.assist_client.async_get_clientsession",
            return_value=session,
        ),
        patch.object(client, "_begin", return_value=_Turn("turn-1", expires_at=1200)),
        patch.object(client, "finish", new=AsyncMock()) as finish,
        pytest.raises(HomeAssistantError),
    ):
        await client.request("conversation", {"messages": []})
    session.post.assert_called_once()
    finish.assert_awaited_once()


@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        (None, False),
        (_Turn("turn-1", expires_at=1001), True),
        (_Turn("turn-1", expires_at=1000), False),
        (_Turn("turn-1", expires_at=1001, failed=True), False),
        (_Turn("turn-1", expires_at=1001, closed=True), False),
    ],
)
def test_native_execution_requires_a_live_reserved_turn(hass, turn, expected):
    with (
        patch("custom_components.hombee.assist_client._TURN") as current,
        patch("custom_components.hombee.assist_client.time", return_value=1000),
    ):
        current.get.return_value = turn
        assert _client(hass).can_execute_tools() is expected
