"""Validate cloud history and native tool continuations before local execution."""

from __future__ import annotations

import json

from homeassistant.components import conversation
from homeassistant.helpers import llm

from . import assist_history


def _assistant(message, agent_id, names, seen_ids, *, external):
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ValueError("Expected assistant message")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ValueError("Invalid assistant content")
    raw_calls = message.get("tool_calls")
    if raw_calls is not None and not isinstance(raw_calls, list):
        raise ValueError("Invalid tool calls")
    calls = []
    for call in raw_calls or []:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ValueError("Invalid tool call")
        call_id = call.get("id")
        function = call.get("function")
        if (
            not isinstance(call_id, str)
            or not call_id
            or call_id in seen_ids
            or not isinstance(function, dict)
        ):
            raise ValueError("Invalid or replayed tool call")
        name = function.get("name")
        if not isinstance(name, str) or (
            (external and (not name.startswith("hombee__") or name in names))
            or (not external and name not in names)
        ):
            raise ValueError("Tool is not available to this executor")
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise ValueError("Invalid tool arguments")
        arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        seen_ids.add(call_id)
        calls.append(
            llm.ToolInput(
                id=call_id, tool_name=name, tool_args=arguments, external=external
            )
        )
    return conversation.AssistantContent(
        agent_id=agent_id, content=content, tool_calls=calls or None
    )


def _cloud_history(messages, agent_id, names, seen_ids):
    history = []
    pending = {}
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Invalid cloud transcript")
        if message.get("role") == "assistant":
            if pending:
                raise ValueError("Missing cloud tool result")
            content = _assistant(message, agent_id, names, seen_ids, external=True)
            if not content.tool_calls:
                raise ValueError("Cloud transcript must contain completed tools")
            pending = {call.id: call.tool_name for call in content.tool_calls}
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise ValueError("Unexpected cloud tool result")
            raw_result = message.get("content")
            if not isinstance(raw_result, str):
                raise ValueError("Invalid cloud tool result")
            data = json.loads(raw_result)
            if not isinstance(data, dict):
                raise ValueError("Cloud tool result must be an object")
            content = assist_history.TOOL_RESULTS.create(
                agent_id, call_id, pending.pop(call_id), data
            )
        else:
            raise ValueError("Unexpected cloud transcript role")
        history.append(content)
    if pending:
        raise ValueError("Missing cloud tool result")
    return history


def decode_response(result, agent_id, names, seen_ids, seen_continuations):
    """Decode an entire response atomically; cloud calls are history only."""
    if not isinstance(result, dict) or not isinstance(result.get("messages"), list):
        raise ValueError("Invalid conversation response")
    seen_ids = set(seen_ids)
    history = _cloud_history(result["messages"], agent_id, names, seen_ids)
    content = _assistant(
        result.get("message"), agent_id, names, seen_ids, external=False
    )
    continuation = result.get("continuation")
    if content.tool_calls:
        if (
            not isinstance(continuation, str)
            or not continuation
            or continuation in seen_continuations
        ):
            raise ValueError("Invalid or replayed continuation")
    elif continuation is not None or not content.content:
        raise ValueError("Invalid completed conversation")
    return history, content, continuation
