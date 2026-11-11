"""Keep native execution and cloud history in one authorized HA conversation."""

import json
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hombee import assist_history
from custom_components.hombee.assist_conversation import decode_response
from custom_components.hombee.assist_notices import failure_notice, limit_notice
from custom_components.hombee.const import DOMAIN
from custom_components.hombee.conversation import _messages


def _call(name, call_id="native-1", arguments=None):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {}),
        },
    }


def _response(calls=None, *, continuation=None, messages=None, content=None):
    return {
        "messages": messages or [],
        "message": {"role": "assistant", "content": content, "tool_calls": calls},
        "continuation": continuation,
    }


def _cloud_history():
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [_call("hombee__workforce_list_tasks", "cloud-1")],
        },
        {
            "role": "tool",
            "tool_call_id": "cloud-1",
            "content": json.dumps({"tasks": [{"id": "task-1", "title": "Inspect"}]}),
        },
    ]


@dataclass
class LegacyToolResultContent:
    """HA 2026.9's public result shape, without the newer result attribute."""

    agent_id: str
    tool_call_id: str
    tool_name: str
    tool_result: dict


@pytest.mark.parametrize("legacy", [True, False])
def test_cloud_and_native_history_roundtrip_on_supported_ha_result_apis(legacy):
    content_type = LegacyToolResultContent if legacy else conversation.ToolResultContent
    adapter = assist_history.ToolResultAdapter(
        content_type, None if legacy else llm.ToolResult
    )
    data = {"error": "permission_denied", "message": "Access changed."}
    cloud_history = _cloud_history()
    cloud_history[-1]["content"] = json.dumps(data)
    with (
        patch.object(assist_history, "TOOL_RESULTS", adapter),
        patch.object(conversation, "ToolResultContent", content_type),
    ):
        history, completed, token = decode_response(
            _response(content="Access changed.", messages=cloud_history),
            "voice",
            set(),
            set(),
            set(),
        )
        native_result = adapter.create(
            "voice", "native-1", "HassTurnOn", {"success": []}
        )
        wire = _messages(SimpleNamespace(content=[*history, native_result, completed]))
    assert token is None
    assert history[0].tool_calls[0].external
    assert json.loads(wire[1]["content"]) == data
    assert wire[1]["is_error"] is True
    assert json.loads(wire[2]["content"]) == {"success": []}
    assert wire[2]["is_error"] is False
    assert wire[3] == {"role": "assistant", "content": "Access changed."}
    if not legacy:
        assert history[1].result.error


@pytest.fixture
async def voice(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="assist", data={"entry_kind": "assist"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen lamp"})
    async_expose_entity(hass, "conversation", "light.kitchen", True)
    actions = []

    async def turn_on(call):
        actions.append(call)

    hass.services.async_register("light", "turn_on", turn_on)
    agent_id = er.async_get(hass).async_get_entity_id(
        "conversation", DOMAIN, "hombee_assist_conversation"
    )
    with (
        patch.object(entry.runtime_data, "begin_conversation", new=AsyncMock()),
        patch.object(entry.runtime_data, "can_execute_tools", return_value=True),
        patch.object(entry.runtime_data, "finish", new=AsyncMock()) as finish,
    ):
        yield entry.runtime_data, agent_id, actions, finish


def _native(payload, call_id="native-1"):
    name = next(
        tool["function"]["name"]
        for tool in payload["tools"]
        if tool["function"]["name"].endswith("HassTurnOn")
    )
    return _call(name, call_id, {"name": "Kitchen lamp", "domain": ["light"]})


async def test_mixed_cloud_native_turn_keeps_history_context_and_followup(hass, voice):
    client, agent_id, actions, _finish = voice
    area = ar.async_get(hass).async_create("Kitchen")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "voice")}
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    requests = []

    async def gateway(stage, payload):
        assert stage == "conversation"
        requests.append(deepcopy(payload))
        assert payload["context"]["deviceId"] == device.id
        assert payload["context"]["areaId"] == area.id
        assert payload["context"]["language"] == "en"
        if len(requests) == 1:
            assert "continuation" not in payload
            return _response(
                [_native(payload)], continuation="continue-1", messages=_cloud_history()
            )
        cloud_results = [
            message
            for message in payload["messages"]
            if message.get("tool_call_id") == "cloud-1"
        ]
        assert len(cloud_results) == 1
        assert json.loads(cloud_results[0]["content"]) == {
            "tasks": [{"id": "task-1", "title": "Inspect"}]
        }
        if len(requests) == 2:
            assert payload["continuation"] == "continue-1"
            assert "light.kitchen" in payload["messages"][-1]["content"]
            return _response(content="One task found; kitchen light is on.")
        assert "continuation" not in payload
        assert payload["messages"][-1]["content"] == "What was its title?"
        return _response(content="Inspect.")

    with patch.object(client, "request", side_effect=gateway):
        first = await conversation.async_converse(
            hass,
            "List my tasks and turn on the kitchen lamp",
            None,
            Context(),
            language="en",
            agent_id=agent_id,
            device_id=device.id,
        )
        second = await conversation.async_converse(
            hass,
            "What was its title?",
            first.conversation_id,
            Context(),
            language="en",
            agent_id=agent_id,
            device_id=device.id,
        )
    assert len(actions) == 1
    assert second.conversation_id == first.conversation_id
    assert second.response.speech["plain"]["speech"] == "Inspect."
    assert requests[0]["context"]["conversationId"] == first.conversation_id


@pytest.mark.parametrize(
    "malformation",
    [
        "duplicate_id",
        "bad_arguments",
        "unknown_native",
        "missing_token",
        "cloud_as_native",
        "unpaired_cloud",
        "local_as_cloud",
    ],
)
async def test_malformed_response_never_executes_partial_native_batch(
    hass, voice, malformation
):
    client, agent_id, actions, finish = voice

    async def gateway(_stage, payload):
        call = _native(payload)
        response = _response([call], continuation="continue-1")
        if malformation == "duplicate_id":
            response["message"]["tool_calls"].append(deepcopy(call))
        elif malformation == "bad_arguments":
            response["message"]["tool_calls"].append(_native(payload, "native-2"))
            response["message"]["tool_calls"][-1]["function"]["arguments"] = "[]"
        elif malformation == "unknown_native":
            call["function"]["name"] = "HassUnexposedService"
        elif malformation == "missing_token":
            response["continuation"] = None
        elif malformation == "cloud_as_native":
            call["function"]["name"] = "hombee__workforce_list_tasks"
        elif malformation == "unpaired_cloud":
            response["messages"] = _cloud_history()[:1]
        else:
            response["messages"] = _cloud_history()
            response["messages"][0]["tool_calls"][0]["function"]["name"] = call[
                "function"
            ]["name"]
        return response

    with patch.object(client, "request", side_effect=gateway):
        result = await conversation.async_converse(
            hass, "Perform actions", None, Context(), language="en", agent_id=agent_id
        )
    assert actions == []
    finish.assert_awaited_once()
    assert result.response.speech["plain"]["speech"] == failure_notice("en")


@pytest.mark.parametrize("replay", ["call_id", "continuation", "backend_failure"])
async def test_continuation_failure_does_not_repeat_completed_native_action(
    hass, voice, replay
):
    client, agent_id, actions, finish = voice
    count = 0

    async def gateway(_stage, payload):
        nonlocal count
        count += 1
        if count == 3:
            assert "continuation" not in payload
            assert payload["messages"][-2]["content"] == failure_notice("pl")
            return _response(content="Check the completed action before retrying.")
        if count == 2 and replay == "backend_failure":
            raise HomeAssistantError("Cloud operation failed")
        return _response(
            [
                _native(
                    payload, "native-1" if replay == "call_id" else f"native-{count}"
                )
            ],
            continuation=(
                "continue-1" if replay == "continuation" else f"continue-{count}"
            ),
        )

    with patch.object(client, "request", side_effect=gateway):
        result = await conversation.async_converse(
            hass, "Perform actions", None, Context(), language="pl", agent_id=agent_id
        )
        assert count == 2
        await conversation.async_converse(
            hass,
            "What happened?",
            result.conversation_id,
            Context(),
            language="pl",
            agent_id=agent_id,
        )
    assert len(actions) == 1
    assert count == 3
    finish.assert_awaited_once()
    assert result.response.speech["plain"]["speech"] == failure_notice("pl")


async def test_more_than_five_native_continuations_keep_one_cloud_execution(
    hass, voice
):
    client, agent_id, actions, _finish = voice
    count = 0
    for index in range(1, 7):
        entity_id = f"light.room_{index}"
        hass.states.async_set(entity_id, "off", {"friendly_name": f"Lamp {index}"})
        async_expose_entity(hass, "conversation", entity_id, True)

    async def gateway(_stage, payload):
        nonlocal count
        count += 1
        if count > 1:
            cloud = [
                item
                for item in payload["messages"]
                if item.get("tool_call_id") == "cloud-1"
            ]
            assert len(cloud) == 1
            assert not cloud[0]["is_error"]
        if count == 7:
            return _response(content="Task listed and six device actions completed.")
        call = _native(payload, f"native-{count}")
        call["function"]["arguments"] = json.dumps(
            {"name": f"Lamp {count}", "domain": ["light"]}
        )
        return _response(
            [call],
            continuation=f"continue-{count}",
            messages=_cloud_history() if count == 1 else [],
        )

    with patch.object(client, "request", side_effect=gateway):
        result = await conversation.async_converse(
            hass,
            "List tasks and do six device actions",
            None,
            Context(),
            language="en",
            agent_id=agent_id,
        )
    assert count == 7
    assert len(actions) == 6
    assert result.response.speech["plain"]["speech"] == (
        "Task listed and six device actions completed."
    )


@pytest.mark.parametrize(
    ("batch_size", "cloud", "executed", "pending", "requests"),
    [(40, False, 40, 1, 2), (39, True, 39, 1, 2), (40, True, 0, 40, 1)],
)
async def test_combined_tool_limit_rejects_entire_native_batch(
    hass, voice, *, batch_size, cloud, executed, pending, requests
):
    client, agent_id, actions, _finish = voice
    count = 0

    async def gateway(_stage, payload):
        nonlocal count
        count += 1
        return _response(
            [
                _native(payload, f"native-{count}-{index}")
                for index in range(batch_size if count == 1 else 1)
            ],
            continuation=f"continue-{count}",
            messages=_cloud_history() if cloud and count == 1 else [],
        )

    with patch.object(client, "request", side_effect=gateway):
        result = await conversation.async_converse(
            hass,
            "Perform many actions",
            None,
            Context(),
            language="en",
            agent_id=agent_id,
        )
    assert len(actions) == executed
    assert count == requests
    assert result.response.speech["plain"]["speech"] == limit_notice(
        "en", executed + int(cloud), 0, pending
    )


async def test_twentieth_response_cannot_start_another_native_action(hass, voice):
    client, agent_id, actions, _finish = voice
    count = 0

    async def gateway(_stage, payload):
        nonlocal count
        count += 1
        return _response(
            [_native(payload, f"native-{count}")], continuation=f"continue-{count}"
        )

    with patch.object(client, "request", side_effect=gateway):
        result = await conversation.async_converse(
            hass, "Continue actions", None, Context(), language="pl", agent_id=agent_id
        )
    assert count == 20
    assert len(actions) == 19
    assert result.response.speech["plain"]["speech"] == limit_notice("pl", 19, 0, 1)


async def test_turn_expiry_after_response_prevents_native_batch(hass, voice):
    client, agent_id, actions, _finish = voice

    async def gateway(_stage, payload):
        return _response(
            [_native(payload)], continuation="continue-1", messages=_cloud_history()
        )

    with (
        patch.object(client, "request", side_effect=gateway),
        patch.object(client, "can_execute_tools", return_value=False),
    ):
        result = await conversation.async_converse(
            hass,
            "List tasks and switch on light",
            None,
            Context(),
            language="en",
            agent_id=agent_id,
        )
    assert actions == []
    assert result.response.speech["plain"]["speech"] == limit_notice("en", 1, 0, 1)


async def test_modern_native_failure_flag_reaches_backend_without_changing_data(
    hass, voice
):
    client, agent_id, actions, _finish = voice
    data = {"message": "The device is unavailable."}
    count = 0

    async def gateway(_stage, payload):
        nonlocal count
        count += 1
        if count == 1:
            return _response([_native(payload)], continuation="continue-1")
        assert payload["messages"][-1]["is_error"] is True
        assert json.loads(payload["messages"][-1]["content"]) == data
        return _response(content="The device action failed; nothing else was run.")

    with (
        patch.object(client, "request", side_effect=gateway),
        patch.object(
            llm.APIInstance,
            "async_call_tool",
            return_value=llm.ToolResult(data=data, error=True),
        ),
    ):
        result = await conversation.async_converse(
            hass,
            "Switch on the light",
            None,
            Context(),
            language="en",
            agent_id=agent_id,
        )
    assert actions == []
    assert result.response.speech["plain"]["speech"] == (
        "The device action failed; nothing else was run."
    )
