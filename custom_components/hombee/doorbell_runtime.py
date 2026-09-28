"""Installation-scoped doorbell configuration and ring delivery."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import instance_id
from homeassistant.helpers.storage import Store

from .doorbell_delivery import DoorbellDelivery
from .doorbell_models import (
    DOORBELL,
    bind_entities,
    discover,
    for_user,
    resolve_entities,
    visible,
)


class DoorbellRuntime:
    """Own installation configuration and ring delivery."""

    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, "hombee.doorbells")
        self.data = {"revision": 0, "doorbells": [], "bindings": {}}
        self.instance_id = ""
        self.lock = asyncio.Lock()
        self.delivery = DoorbellDelivery(hass, self)

    async def load(self):
        self.instance_id = await instance_id.async_get(self.hass)
        self.data = await self.store.async_load() or self.data
        await self.delivery.load()
        unsubscribe = self.hass.bus.async_listen(
            EVENT_STATE_CHANGED, self.state_changed
        )

        @callback
        def stop(_event):
            unsubscribe()
            self.delivery.stop()

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)

    def doorbells(self):
        return resolve_entities(
            self.hass, self.data["doorbells"], self.data["bindings"]
        )

    def snapshot(self, user):
        suggested, candidates = (
            discover(self.hass, self.instance_id) if user.is_admin else ([], [])
        )
        configured = self.doorbells()
        ids = {doorbell["id"] for doorbell in configured}
        return {
            "protocol": 1,
            "instanceId": self.instance_id,
            "revision": self.data["revision"],
            "configured": bool(self.data.get("pairing")),
            "canManage": user.is_admin,
            "diagnostic": self.delivery.diagnostic,
            "doorbells": [
                for_user(user, doorbell)
                for doorbell in configured
                if visible(user, doorbell)
            ],
            "discovered": [
                doorbell for doorbell in suggested if doorbell["id"] not in ids
            ],
            "candidates": candidates,
        }

    async def configure(self, pairing):
        async with self.lock:
            updated = {**self.data, "pairing": pairing}
            await self.store.async_save(updated)
            self.data = updated

    async def save(self, expected_revision, request_id, doorbells):
        validated = vol.All([DOORBELL], vol.Length(max=50))(doorbells)
        if len({doorbell["id"] for doorbell in validated}) != len(validated):
            raise vol.Invalid("Doorbell IDs must be unique")
        async with self.lock:
            if self.data.get("lastRequest") == request_id:
                return
            if self.data["revision"] != expected_revision:
                raise vol.Invalid("Doorbell settings changed. Refresh and try again")
            bindings = bind_entities(self.hass, validated)
            updated = {
                **self.data,
                "doorbells": validated,
                "bindings": bindings,
                "revision": expected_revision + 1,
                "lastRequest": request_id,
            }
            await self.store.async_save(updated)
            self.data = updated

    @callback
    def state_changed(self, event):
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (
            old is None
            or new is None
            or old.state in ("unknown", "unavailable")
            or new.state in ("unknown", "unavailable")
        ):
            return
        for doorbell in self.doorbells():
            source = doorbell["source"]
            if not doorbell["enabled"] or source["entityId"] != new.entity_id:
                continue
            matched = False
            if source["kind"] == "event":
                matched = (
                    old.state != new.state
                    and new.attributes.get("event_type") == source["eventType"]
                )
                try:
                    matched = (
                        matched
                        and abs(
                            time.time() - datetime.fromisoformat(new.state).timestamp()
                        )
                        <= 60
                    )
                except ValueError:
                    matched = False
            elif source["kind"] == "state":
                matched = (
                    old.state == source["fromState"] and new.state == source["toState"]
                )
            if matched:
                source_id = new.attributes.get("event_id") or event.context.id
                self.hass.async_create_task(
                    self.ring(doorbell["id"], str(source_id)), "hombee-doorbell-ring"
                )

    async def ring(self, doorbell_id, source_id):
        doorbell = next(
            (
                item
                for item in self.doorbells()
                if item["id"] == doorbell_id and item["enabled"]
            ),
            None,
        )
        if doorbell is None:
            raise HomeAssistantError("Doorbell is not enabled")
        event_id = str(
            uuid5(NAMESPACE_URL, f"{self.instance_id}:{doorbell_id}:{source_id}")
        )
        await self.delivery.enqueue(
            {
                "version": 1,
                "eventId": event_id,
                "doorbellId": doorbell_id,
                "occurredAt": datetime.now(UTC).isoformat(),
                "image": None,
            },
            next(iter(doorbell["cameras"]), None),
        )
