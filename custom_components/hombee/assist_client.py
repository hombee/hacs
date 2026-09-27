"""Turn-scoped cloud requests and content-free operational diagnostics."""

from __future__ import annotations

from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic, time
from typing import Any
from uuid import uuid4

from aiohttp import ClientError, ClientTimeout
from homeassistant.components import persistent_notification
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession


@dataclass
class _Turn:
    id: str
    expires_at: float = 0
    failed: bool = False
    conversation_started: bool = False
    closed: bool = False


_TURN: ContextVar[_Turn | None] = ContextVar("hombee_assist_turn", default=None)


class HombeeAssistClient:
    """Carry one reservation through the native pipeline task and its TTS task."""

    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.diagnostic: dict[str, Any] | None = None

    async def begin_conversation(self) -> None:
        """A follow-up is a new turn, even when it shares a conversation ID."""
        current = _TURN.get()
        await self._begin(force=current is not None and current.conversation_started)
        if current := _TURN.get():
            current.conversation_started = True

    async def _begin(self, *, force=False) -> _Turn:
        current = _TURN.get()
        if not force and current and not current.closed:
            if current.failed or current.expires_at <= time():
                raise HomeAssistantError("This voice turn could not be completed.")
            return current
        current = _Turn(str(uuid4()))
        _TURN.set(current)
        try:
            result = await self._send("turn", {}, current.id)
            current.expires_at = result["expiresAt"] / 1000
        except HomeAssistantError, KeyError, TypeError:
            current.failed = True
            raise
        return current

    async def finish(self) -> None:
        """Release unused capacity; ambiguous in-flight costs stay reserved."""
        current = _TURN.get()
        if current is None or current.closed:
            return
        current.closed = True
        # The backend also expires unused capacity after five minutes.
        with suppress(HomeAssistantError):
            await self._send("finish", {}, str(uuid4()), turn=current, diagnose=False)

    async def request(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Reserve before recognition/actions and never retry provider writes."""
        current = await self._begin(force=stage == "transcription")
        try:
            result = await self._send(stage, payload, str(uuid4()), turn=current)
        except HomeAssistantError:
            current.failed = True
            await self.finish()
            raise
        if stage == "speech":
            await self.finish()
        return result

    async def _send(self, stage, payload, request_id, *, turn=None, diagnose=True):
        data = self.entry.data
        started = monotonic()
        support_id = request_id
        code = "connection_unavailable"
        try:
            if not data.get("token"):
                code = "not_associated"
                raise HomeAssistantError(
                    "Repair this instance in Hombee Pro configuration."
                )
            headers = {
                "Authorization": f"Bearer {data['token']}",
                "Idempotency-Key": request_id,
            }
            if turn is not None:
                headers["X-Hombee-Assist-Turn"] = turn.id
            url = (
                f"{data['gatewayUrl']}/api/assist/instances/"
                f"{data['instanceId']}/{stage}"
            )
            async with async_get_clientsession(self.hass).post(
                url,
                json=payload,
                headers=headers,
                timeout=ClientTimeout(total=40),
                allow_redirects=False,
            ) as response:
                result = await response.json()
                if response.status != 200:
                    code = result.get("code", "assist_unavailable")
                    support_id = result.get("supportId", support_id)
                    raise HomeAssistantError(
                        result.get("message", "Hombee Voice is unavailable.")
                    )
                if diagnose:
                    self.diagnostic = None
                    persistent_notification.async_dismiss(self.hass, "hombee_voice")
                return result
        except (ClientError, TimeoutError, ValueError, HomeAssistantError) as error:
            if diagnose:
                self.diagnostic = {
                    "code": code,
                    "stage": stage,
                    "durationMs": round((monotonic() - started) * 1000),
                    "occurredAt": datetime.now(UTC).isoformat(),
                    "supportId": support_id,
                }
                persistent_notification.async_create(
                    self.hass,
                    f"Open Pro configuration in Hombee for usage, subscription status "
                    f"and connection repair. Code: {code}. "
                    f"Support reference: {support_id}.",
                    title="Hombee Voice needs attention",
                    notification_id="hombee_voice",
                )
            raise HomeAssistantError(
                "Hombee Voice could not complete this request. Check Pro configuration."
            ) from error
