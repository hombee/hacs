"""Bounded, persisted outbound delivery; camera failure never suppresses a ring."""

from __future__ import annotations

import asyncio
import base64
import time
from datetime import timedelta

from aiohttp import ClientError, ClientTimeout
from homeassistant.components.camera import async_get_image
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

LIFETIME_SECONDS = 60
MAX_IMAGE_BYTES = 256_000


class DoorbellDelivery:
    """Own one serialized delivery lane and preserve retries over HA restarts."""

    def __init__(self, hass, runtime):
        self.hass = hass
        self.runtime = runtime
        self.store = Store(hass, 1, "hombee.doorbell_outbox")
        self.jobs = []
        self.lock = asyncio.Lock()
        self.unsubscribe = None
        self.diagnostic = None

    async def load(self):
        self.jobs = await self.store.async_load() or []
        self.unsubscribe = async_track_time_interval(
            self.hass, self.tick, timedelta(seconds=5)
        )

    @callback
    def tick(self, _now):
        self.hass.async_create_task(self.drain(), "hombee-doorbell-delivery")

    async def enqueue(self, event, camera):
        async with self.lock:
            if any(job["event"]["eventId"] == event["eventId"] for job in self.jobs):
                return
            now = time.time()
            self.jobs = [job for job in self.jobs if job["expires"] > now][-19:]
            self.jobs.append(
                {
                    "event": event,
                    "camera": camera,
                    "expires": now + LIFETIME_SECONDS,
                    "attempts": 0,
                    "nextAttempt": now,
                    "captured": False,
                }
            )
            await self.store.async_save(self.jobs)
        await self.drain()

    async def drain(self):
        if self.lock.locked():
            return
        async with self.lock:
            pairing = self.runtime.data.get("pairing")
            if not pairing:
                return
            session = async_get_clientsession(self.hass)
            remaining = []
            for job in self.jobs:
                now = time.time()
                if job["expires"] <= now or job["attempts"] >= 6:
                    continue
                if job["nextAttempt"] > now:
                    remaining.append(job)
                    continue
                job["attempts"] += 1
                job["nextAttempt"] = now + min(20, 2 ** job["attempts"])
                await self.store.async_save(self.jobs)
                if not job["captured"]:
                    job["captured"] = True
                    if job["camera"]:
                        try:
                            async with asyncio.timeout(2):
                                image = await async_get_image(
                                    self.hass, job["camera"], width=640
                                )
                            if (
                                image.content_type in ("image/jpeg", "image/png")
                                and len(image.content) <= MAX_IMAGE_BYTES
                            ):
                                job["event"]["image"] = {
                                    "cameraId": job["camera"],
                                    "contentType": image.content_type,
                                    "base64": base64.b64encode(image.content).decode(
                                        "ascii"
                                    ),
                                }
                        except TimeoutError, ClientError, OSError, HomeAssistantError:
                            pass
                    await self.store.async_save(self.jobs)
                try:
                    url = (
                        f'{pairing["gatewayUrl"]}/api/doorbells/instances/'
                        f"{self.runtime.instance_id}/events"
                    )
                    async with session.post(
                        url,
                        json=job["event"],
                        headers={"Authorization": f'Bearer {pairing["token"]}'},
                        timeout=ClientTimeout(total=8),
                    ) as response:
                        if response.status in (200, 202, 204):
                            self.diagnostic = None
                            continue
                        self.diagnostic = f"Delivery returned {response.status}"
                        if response.status not in (408, 429) and response.status < 500:
                            continue
                except TimeoutError, ClientError, OSError:
                    self.diagnostic = "Doorbell delivery is waiting for a connection"
                remaining.append(job)
            self.jobs = remaining
            await self.store.async_save(self.jobs)

    def stop(self):
        if self.unsubscribe:
            self.unsubscribe()
