"""Handle gateway tool requests after Assist supplies the user's HA context."""

import asyncio
import json
from dataclasses import replace

from aiohttp import ClientError
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm

from . import assist_history
from .assist_conversation import decode_response


def _home_context(offset, tools, context, chat_log):
    if type(offset) is not int or offset < 0:
        return {"error": "Invalid context offset."}
    catalog = json.dumps(
        {
            "tools": [t["function"] for t in tools],
            "room": context["areaId"],
            "instructions": chat_log.content[0].content,
        },
        ensure_ascii=False,
    )
    end = offset + 10000
    return {
        "page": catalog[offset:end],
        "next_offset": end if end < len(catalog) else None,
    }


async def _call_native(call, chat_log, agent_id):
    args = call["args"]
    try:
        arguments = json.loads(args["arguments"])
        if not isinstance(arguments, dict):
            raise ValueError("Expected an object")
        request = llm.ToolInput(
            id=call["id"], tool_name=args["name"], tool_args=arguments
        )
        content = conversation.AssistantContent(agent_id=agent_id, tool_calls=[request])
        async for result in chat_log.async_add_assistant_content(content):
            return {
                "result": assist_history.TOOL_RESULTS.data(result),
                "error": assist_history.TOOL_RESULTS.error(result),
            }
        raise ValueError("Missing tool result")
    except ValueError, TypeError, KeyError, HomeAssistantError:
        return {"error": "Home Assistant could not execute that tool."}


async def live_conversation(client, live, user_input, chat_log, *, tools, context):
    api = chat_log.llm_api
    user_index = len(chat_log.content) - 1
    original_input = chat_log.content[user_index]
    local_attempted = False
    local_matched = False

    async def match_sentence():
        nonlocal local_attempted, local_matched
        if not live.final_transcript or not live.transcript.strip():
            return {"matched": False}
        if local_attempted or api is None:
            return {"error": "Local matching is unavailable or already attempted."}
        local_attempted = True
        response = await conversation.async_handle_intents(
            client.hass, replace(user_input, text=live.transcript.strip()), chat_log
        )
        local_matched = response is not None
        return {
            "matched": local_matched,
            "response": response.as_dict() if response else None,
        }

    async def tool(call):
        name = call["name"]
        args = call["args"]
        if name == "home_command":
            return await match_sentence()
        if name == "end_conversation":
            return {"ended": True}
        if name == "home_context":
            return _home_context(args.get("offset", 0), tools, context, chat_log)
        if name == "hombee_request":
            # Preserve the cloud agent's authorization, tool policy and mutation IDs.
            result = await client.request(
                "conversation",
                {
                    "messages": [{"role": "user", "content": args["request"]}],
                    "tools": [],
                    "context": context,
                },
            )
            history, answer, _ = decode_response(
                result, user_input.agent_id, set(), set(), set()
            )
            for item in history:
                chat_log.async_add_assistant_content_without_tools(item)
            return {"answer": answer.content}
        if name != "call_home_tool" or api is None or local_matched:
            return {"error": "Tool unavailable."}
        return await _call_native(call, chat_log, user_input.agent_id)

    def spoken_delta(text):
        live.output.transcript(text, chat_log)

    try:
        async with asyncio.timeout(90):
            follow_up = await live.reply(tool, spoken_delta)
    except BaseException as error:
        await live.close()
        if isinstance(error, (ClientError, TimeoutError)):
            raise HomeAssistantError("Hombee Voice lost its connection.") from error
        raise
    if (
        isinstance(original_input, conversation.UserContent)
        and live.final_transcript
        and live.transcript.strip()
        and live.transcript.strip() != original_input.content
    ):
        chat_log.content[user_index] = replace(
            original_input, content=live.transcript.strip()
        )
    chat_log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(
            agent_id=user_input.agent_id, content=live.spoken.strip()
        )
    )
    response = intent.IntentResponse(language=user_input.language)
    response.async_set_speech(live.spoken.strip() or (" " if live.size else ""))
    return conversation.ConversationResult(
        response=response,
        conversation_id=chat_log.conversation_id,
        continue_conversation=follow_up,
    )
