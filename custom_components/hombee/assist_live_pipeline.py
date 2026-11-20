"""Connect native reply audio to the playback stream of the running Hombee pipeline."""

from dataclasses import dataclass

from homeassistant.components import tts
from homeassistant.components.assist_pipeline import (
    PipelineEvent,
    PipelineEventType,
    PipelineRun,
)
from homeassistant.components.assist_pipeline.pipeline import KEY_ASSIST_PIPELINE
from homeassistant.helpers.chat_session import current_session

from .assist import assist_pipeline_ready


@dataclass
class LiveOutput:
    stream: tts.ResultStream
    satellite: str | None
    run: PipelineRun | None

    def start(self, source):
        self.stream.async_set_message_stream(source)
        if self.run is not None:
            # ESPHome consumes this native event to begin reading the existing TTS URL.
            self.run.process_event(
                PipelineEvent(
                    PipelineEventType.INTENT_PROGRESS, {"tts_start_streaming": True}
                )
            )

    def transcript(self, text, chat_log):
        delta = {"role": "assistant", "content": text}
        if self.run is not None:
            self.run.process_event(
                PipelineEvent(
                    PipelineEventType.INTENT_PROGRESS, {"chat_log_delta": delta}
                )
            )
        elif chat_log.delta_listener:
            chat_log.delta_listener(chat_log, delta)


def active_output(client):
    session = current_session.get()
    if session is None or not assist_pipeline_ready(client.hass, client.entry):
        return None
    data = client.hass.data[KEY_ASSIST_PIPELINE]
    pipeline_id = client.entry.options.get("pipeline_id")
    traces = data.pipeline_debug.get(pipeline_id, {})
    # Core exposes the TTS stream by token. The run registry supplies early-playback
    # notifications on stable HA. Normal TTS delivery also works without the registry.
    active = getattr(data.pipeline_runs, "_pipeline_runs", {}).get(pipeline_id, {})
    matches = []
    for run_id, trace in reversed(traces.items()):
        if not trace.events or trace.events[-1].type == PipelineEventType.RUN_END:
            continue
        start = trace.events[0].data or {}
        output = start.get("tts_output", {})
        if (
            start.get("conversation_id") == session.conversation_id
            and output.get("token")
            and (stream := tts.async_get_stream(client.hass, output["token"]))
        ):
            matches.append(
                LiveOutput(stream, start.get("satellite_id"), active.get(run_id))
            )
    return matches[0] if len(matches) == 1 else None
