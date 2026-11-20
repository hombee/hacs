"""Carry one native-audio interaction across the existing Assist stages."""

from __future__ import annotations

import asyncio
import json
import struct
from collections import deque
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from aiohttp import ClientError, WSMsgType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pymicro_vad import MicroVad

# Unknown lengths let HA transcode or play this WAV as PCM arrives.
_WAV = struct.pack(
    "<4sI4s4sIHHIIHH4sI",
    b"RIFF",
    0xFFFFFFFF,
    b"WAVE",
    b"fmt ",
    16,
    1,
    1,
    24000,
    48000,
    2,
    16,
    b"data",
    0xFFFFFFFF,
)


async def follow_up_audio(stream):
    """Wait locally for speech onset and retain the start of the first word."""
    frames = deque(maxlen=40)
    pending = bytearray()
    vad = MicroVad()
    source = aiter(stream)
    heard = 0
    async with asyncio.timeout(7):
        async for chunk in source:
            pending.extend(chunk)
            while len(pending) >= 320:
                frame = bytes(pending[:320])
                del pending[:320]
                frames.append(frame)
                heard = heard + 1 if (vad.Process10ms(frame) or 0) > 0.5 else 0
                if heard >= 3:

                    async def audio():
                        yield b"".join(frames) + pending
                        async for part in source:
                            yield part

                    return audio()
    return None


@dataclass
class LiveChat:
    key: str
    resume_until: float = 0.0

    @property
    def resumed(self):
        return self.resume_until > monotonic()


class LiveTurn:
    """Pass the gateway stream from STT to conversation when the speaker finishes."""

    def __init__(self, client, turn, chat, socket, output):
        self.output = output
        self.client = client
        self.turn = turn
        self.chat = chat
        self.socket = socket
        self.transcript = ""
        self.final_transcript = False
        self.spoken = ""
        self.first = None
        self.marker = "Hombee voice audio " + str(uuid4()) + "/" + turn.id
        self.audio = asyncio.Queue()
        self.size = 0
        self.closed = False
        self.started_playback = False
        self.pipeline_task = asyncio.current_task()
        self.pipeline_task.add_done_callback(self.pipeline_ended)
        self.timer = asyncio.get_running_loop().call_later(95, self.expire)

    def pipeline_ended(self, _task):
        # A sentence trigger or cancelled pipeline can finish without calling our agent.
        if not self.closed:
            self.expire()

    def expire(self):
        self.client.hass.async_create_task(self.close())

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.cancel()
        self.pipeline_task.remove_done_callback(self.pipeline_ended)
        self.turn.live = None
        self.client.audio_replies.pop(self.marker, None)
        self.client.active_voice.discard(self)
        self.audio.put_nowait(None)
        await self.socket.close()
        await self.client.finish(self.turn)

    async def message(self):
        frame = await self.socket.receive()
        if frame.type == WSMsgType.BINARY:
            return frame.data
        if frame.type == WSMsgType.TEXT:
            data = json.loads(frame.data)
            if isinstance(data, dict):
                return data
        raise HomeAssistantError("The Hombee voice stream ended early.")

    def record_transcript(self, transcription):
        self.transcript += transcription.get("text", "")
        self.final_transcript |= bool(transcription.get("finished"))

    async def listen(self, stream):
        async def send():
            length = 0
            pending = bytearray()
            async for chunk in stream:
                length += len(chunk)
                if length > 960000:
                    raise HomeAssistantError("Voice input exceeded 30 seconds.")
                pending.extend(chunk)
                # 40 ms frames amortize websocket and base64 framing overhead.
                while len(pending) >= 1280:
                    await self.socket.send_bytes(bytes(pending[:1280]))
                    del pending[:1280]
            if pending:
                await self.socket.send_bytes(bytes(pending))
            await self.socket.send_json({"endInput": True})

        async def receive():
            while True:
                item = await self.message()
                if isinstance(item, dict) and "transcription" in item:
                    self.record_transcript(item["transcription"])
                    if self.final_transcript and self.transcript.strip():
                        return
                else:
                    self.first = item
                    return

        if await self.message() != {"ready": True}:
            raise HomeAssistantError("Voice gateway did not become ready.")
        async with asyncio.TaskGroup() as group:
            sender = group.create_task(send())
            try:
                await receive()
            finally:
                sender.cancel()
        return self.transcript.strip() or "[Voice]"

    async def playback(self):
        """Consume reply audio once, including samples produced before TTS started."""
        if self.started_playback:
            raise HomeAssistantError("This reply is already playing.")
        self.started_playback = True
        try:
            yield _WAV
            while (part := await self.audio.get()) is not None:
                yield part
            if self.closed:
                raise HomeAssistantError("The voice reply was interrupted.")
        finally:
            await self.close()

    async def reply(self, on_tool, on_text):
        self.pipeline_task.remove_done_callback(self.pipeline_ended)
        item = self.first
        seen = set()
        while True:
            if item is None:
                item = await self.message()
            if isinstance(item, bytes):
                self.size += len(item)
                if self.size > 4320000:
                    raise HomeAssistantError("Voice output exceeded its limit.")
                self.audio.put_nowait(item)
                if self.marker not in self.client.audio_replies:
                    self.client.audio_replies[self.marker] = self

                    async def reference():
                        yield self.marker

                    self.output.start(reference())
            elif "call" in item:
                call = item["call"]
                if (
                    call["id"] in seen
                    or len(seen) >= 40
                    or not self.client.can_execute_tools()
                ):
                    raise HomeAssistantError("Invalid or expired voice tool request.")
                seen.add(call["id"])
                result = await on_tool(call)
                if len(json.dumps(result)) > 22000:
                    result = {"error": "Tool result exceeds the voice context limit."}
                await self.socket.send_json({"id": call["id"], "response": result})
            elif "transcription" in item:
                self.record_transcript(item["transcription"])
            elif "spoken" in item:
                self.spoken += item["spoken"]
                if self.size:
                    on_text(item["spoken"])
            elif item.get("done"):
                self.audio.put_nowait(None)
                self.chat.resume_until = 0 if item.get("ended") else monotonic() + 85
                await self.client.finish(self.turn)
                return self.chat.resumed
            item = None


async def start_live(client, session, language, stream, *, target, output):
    chats = client.voice_chats
    identity = (session.conversation_id, output.satellite)
    if (chat := chats.get(identity)) is None:
        chat = chats[identity] = LiveChat(str(uuid4()))
        session.async_on_cleanup(lambda: chats.pop(identity, None))
    if chat.resume_until and not chat.resumed:
        chat.key = str(uuid4())
        chat.resume_until = 0
    if chat.resumed:
        try:
            stream = await follow_up_audio(stream)
        except TimeoutError:
            stream = None
        if stream is None:
            await client.end_voice_chat(chat.key)
            chat.resume_until = 0
            chat.key = str(uuid4())
            return ""
    turn = await client.begin_voice(target)
    data = client.entry.data
    url = f"{data['gatewayUrl']}/api/assist/instances/{data['instanceId']}/live"
    live = None
    try:
        async with asyncio.timeout(38):
            socket = await async_get_clientsession(client.hass).ws_connect(
                url,
                headers={
                    "Authorization": f"Bearer {data['token']}",
                    "X-Hombee-Assist-Turn": turn.id,
                    "X-Hombee-Live-Session": chat.key,
                    "X-Hombee-Resume": "1" if chat.resumed else "0",
                    "X-Hombee-Language": language,
                },
                max_msg_size=524288,
            )
            live = LiveTurn(client, turn, chat, socket, output)
            client.active_voice.add(live)
            turn.live = live
            return await live.listen(stream)
    except BaseException as error:
        chat.resume_until = 0
        chat.key = str(uuid4())
        if live is not None:
            await live.close()
        else:
            await client.finish(turn)
        if isinstance(error, (ClientError, TimeoutError, ExceptionGroup)):
            raise HomeAssistantError("Hombee Voice could not receive audio.") from error
        raise
