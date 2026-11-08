"""Serialized, durable latest-wins telemetry with bounded retries and revocation."""

from __future__ import annotations

import asyncio
import time
from email.utils import parsedate_to_datetime

from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

AUTHORIZATION_LEASE_SECONDS = 24 * 60 * 60


class EnergyDelivery:
    """At most one pending live observation per binding; no historical replay."""

    def __init__(self, hass, runtime):
        self.hass, self.runtime = hass, runtime
        self.store = Store(hass, 1, f"{runtime.store.key}.outbox")
        self.data = {
            "sequence": 0,
            "jobs": {},
            "nextAttempt": 0,
            "attempts": 0,
            "revoked": False,
            "bindings": {},
            "configRevision": -1,
            "authorizationRevision": 0,
        }
        self.lock = asyncio.Lock()
        self.diagnostic = None

    async def load(self):
        """Pending observations keep their original sample times across restart."""
        self.data = {**self.data, **(await self.store.async_load() or {})}
        self._prune()
        await self.store.async_save(self.data)
        await self.reconcile()

    def _prune(self):
        now = time.time() * 1000
        self.data["jobs"] = {
            key: job
            for key, job in self.data["jobs"].items()
            if 0 <= now - job["observedAtMs"] <= 30_000
        }

    async def enqueue(self, observations):
        """Persist before dispatch and replace superseded values, even in outages."""
        async with self.lock:
            publisher = self.runtime.data.get("publisher")
            if not publisher or self.data["revoked"]:
                return
            self._prune()
            for value in observations:
                if self.data["bindings"].get(value["bindingId"], {}).get("revoked"):
                    continue
                self.data["sequence"] += 1
                self.data["jobs"][value["bindingId"]] = {
                    **value,
                    "protocol": 1,
                    "instanceId": self.runtime.instance_id,
                    "publisherId": self.runtime.publisher_id,
                    "generation": publisher["generation"],
                    "sessionId": self.runtime.session_id,
                    "sequence": self.data["sequence"],
                    "bindingRevision": publisher["bindingRevision"],
                }
            await self.store.async_save(self.data)

    async def reconcile(self):
        """Recover a config/outbox write interruption before acknowledging retries.

        The config store is authoritative. Its revision makes this recovery
        idempotent; old operation retries cannot renew authorization leases.
        """
        async with self.lock:
            if self.data["configRevision"] == self.runtime.data["revision"]:
                return
            updated = {**self.data, "jobs": {}, "nextAttempt": 0, "attempts": 0}
            authority = self.runtime.data.get("authorizationRevision", 0)
            states = {
                item["bindingId"]: dict(
                    self.data["bindings"].get(
                        item["bindingId"], {"authorizedUntil": 0, "revoked": False}
                    )
                )
                for item in self.runtime.data["bindings"]
            }
            if authority != self.data["authorizationRevision"]:
                updated["revoked"] = False
                states = {
                    key: {
                        "authorizedUntil": self.runtime.data.get("configuredAt", 0)
                        + AUTHORIZATION_LEASE_SECONDS,
                        "revoked": False,
                    }
                    for key in states
                }
            updated["bindings"] = states
            if not self.runtime.data.get("publisher"):
                updated.update(bindings={}, revoked=False)
            updated.update(
                configRevision=self.runtime.data["revision"],
                authorizationRevision=authority,
            )
            await self.store.async_save(updated)
            self.data = updated
            self.diagnostic = None

    @staticmethod
    def retry_after(value, now):
        """Honor both HTTP Retry-After forms, bounded to one hour."""
        try:
            seconds = float(value)
        except ValueError, TypeError:
            try:
                seconds = parsedate_to_datetime(value).timestamp() - now
            except ValueError, TypeError, OverflowError:
                return 0
        return min(3600, max(0, seconds))

    def current(self, publisher):
        """HTTP authority is exactly the token, generation, and config revision."""
        current = self.runtime.data.get("publisher")
        return current is not None and all(
            current[key] == publisher[key]
            for key in ("token", "generation", "bindingRevision")
        )

    async def drain(self):  # noqa: PLR0912
        """One in-flight POST; failures never queue unbounded work or leak tokens."""
        if self.lock.locked():
            return
        async with self.lock:
            self._prune()
            publisher = self.runtime.data.get("publisher")
            if (
                not publisher
                or self.data["revoked"]
                or time.time() < self.data["nextAttempt"]
            ):
                return
            session = async_get_clientsession(self.hass)
            for binding_id, job in sorted(
                self.data["jobs"].items(), key=lambda item: item[1]["sequence"]
            ):
                if (
                    job["generation"] != publisher["generation"]
                    or job["bindingRevision"] != publisher["bindingRevision"]
                    or not 0 <= time.time() * 1000 - job["observedAtMs"] <= 30_000
                ):
                    self.data["jobs"].pop(binding_id, None)
                    continue
                retry = 0
                try:
                    async with session.post(
                        f'{publisher["url"]}/api/energy/local/instances/{self.runtime.instance_id}/observations',
                        json=job,
                        headers={"Authorization": f'Bearer {publisher["token"]}'},
                        timeout=ClientTimeout(total=8),
                        allow_redirects=False,
                    ) as response:
                        # Config can change while HTTP is in flight. A response
                        # for the old token or revision cannot affect its successor.
                        if not self.current(publisher):
                            self.data["jobs"].pop(binding_id, None)
                            continue
                        if response.status == 409:
                            try:
                                payload = await response.json()
                            except ValueError, ClientError:
                                payload = {}
                            if not self.current(publisher):
                                self.data["jobs"].pop(binding_id, None)
                                continue
                            if (
                                isinstance(payload, dict)
                                and payload.get("error") == "binding_obsolete"
                            ):
                                self.data["bindings"][binding_id] = {
                                    "authorizedUntil": 0,
                                    "revoked": True,
                                }
                                self.diagnostic = (
                                    f"{binding_id}:binding_authorization_required"
                                )
                            self.data["jobs"].pop(binding_id, None)
                            continue
                        if response.status in (200, 202, 204):
                            self.data["jobs"].pop(binding_id, None)
                            self.data.update(attempts=0, nextAttempt=0)
                            self.data["bindings"].setdefault(
                                binding_id, {"revoked": False}
                            )["authorizedUntil"] = (
                                time.time() + AUTHORIZATION_LEASE_SECONDS
                            )
                            self.diagnostic = None
                            continue
                        if response.status in (401, 403):
                            self.data.update(revoked=True, jobs={})
                            self.diagnostic = "publisher_authorization_required"
                            break
                        self.diagnostic = f"publisher_http_{response.status}"
                        if response.status not in (408, 429) and response.status < 500:
                            self.data["jobs"].pop(binding_id, None)
                            continue
                        retry = self.retry_after(
                            response.headers.get("Retry-After"), time.time()
                        )
                except TimeoutError, ClientError, OSError:
                    self.diagnostic = "publisher_connection_unavailable"
                self.data["attempts"] = min(10, self.data["attempts"] + 1)
                self.data["nextAttempt"] = time.time() + max(
                    retry, min(300, 2 ** self.data["attempts"])
                )
                break
            await self.store.async_save(self.data)
