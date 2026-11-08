"""Always-on local energy runtime, independent of Voice, Pro, and phone lifetime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import voluptuous as vol
from homeassistant.const import (
    EVENT_CALL_SERVICE,
    EVENT_HOMEASSISTANT_STOP,
    EVENT_STATE_CHANGED,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import instance_id
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from .adapter import observation
from .delivery import EnergyDelivery
from .discovery import discover
from .models import BINDING, PUBLISHER
from .policy import PolicyEngine


class PublisherRuntime:
    """Own durable config, registry bindings, local decisions and publication."""

    def __init__(self, hass, owner, publisher_id):
        self.hass = hass
        self.owner = owner
        self.publisher_id = publisher_id
        storage_id = hashlib.sha256(publisher_id.encode()).hexdigest()
        self.store = Store(hass, 1, f"hombee.energy.{storage_id}")
        self.data = {
            "revision": 0,
            "bindings": [],
            "actions": {},
            "overrides": {},
            "requests": {},
        }
        self.instance_id = ""
        self.session_id = str(uuid4())
        self.lock = asyncio.Lock()
        self.sample_lock = asyncio.Lock()
        self.delivery = EnergyDelivery(hass, self)
        self.policy = PolicyEngine(self)
        self.pending_overrides = set()
        self.task = None
        self.stopped = False
        self.version = "unknown"

    async def load(self):
        self.instance_id = await instance_id.async_get(self.hass)
        self.data = await self.store.async_load() or self.data
        integration = await async_get_integration(self.hass, "hombee")
        self.version = str(integration.version) if integration.version else "unknown"
        await self.delivery.load()
        unsubscribe_state = self.hass.bus.async_listen(
            EVENT_STATE_CHANGED, self.state_changed
        )
        unsubscribe_service = self.hass.bus.async_listen(
            EVENT_CALL_SERVICE, self.service_called
        )
        unsubscribe_interval = async_track_time_interval(
            self.hass, self.tick, timedelta(seconds=10)
        )

        @callback
        def stop(_event):
            self.stopped = True
            unsubscribe_state()
            unsubscribe_service()
            unsubscribe_interval()
            if self.task:
                self.task.cancel()

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)

    @callback
    def state_changed(self, event):
        """Process manual changes immediately; heartbeat coalesces telemetry polls."""
        if (
            not self.stopped
            and self.data["bindings"]
            and event.data.get("entity_id")
            in {self.resolve_action(key) for key in self.data["actions"]}
        ):
            old, new = event.data.get("old_state"), event.data.get("new_state")
            if (
                old is not None
                and new is not None
                and old.state != new.state
                and old.state in ("on", "off")
                and new.state in ("on", "off")
                and event.context.id not in self.policy.contexts
            ):
                self.pending_overrides.update(
                    key
                    for key in self.data["actions"]
                    if self.resolve_action(key) == new.entity_id
                )
            self.hass.async_create_task(
                self._manual_changed(event), "hombee-energy-manual-override"
            )

    async def _manual_changed(self, event):
        async with self.lock:
            await self.policy.changed(event)

    @callback
    def service_called(self, event):
        """An explicit switch command can override even an unchanged target."""
        if (
            self.stopped
            or event.data.get("domain") != "switch"
            or event.data.get("service") not in ("turn_on", "turn_off", "toggle")
            or event.context.id in self.policy.contexts
        ):
            return
        targets = event.data.get("service_data", {}).get("entity_id", [])
        targets = [targets] if isinstance(targets, str) else targets
        keys = [
            key
            for key in self.data["actions"]
            if self.resolve_action(key) in targets or "all" in targets
        ]
        if keys:
            # This callback runs during a preceding rule's service await. Mark
            # immediately so a later rule cannot undo a user's queued command.
            self.pending_overrides.update(keys)
            self.hass.async_create_task(
                self._manual_command(keys), "hombee-energy-manual-command"
            )

    async def _manual_command(self, keys):
        async with self.lock:
            self.data["overrides"].update(
                {key: True for key in keys if key in self.data["actions"]}
            )
            await self.store.async_save(self.data)

    @callback
    def tick(self, _now):
        if not self.stopped and (self.task is None or self.task.done()):
            self.task = self.hass.async_create_task(
                self.sample(), "hombee-energy-sample"
            )

    async def sample(self):
        """One sample per ten-second heartbeat, even when unchanged or offline."""
        async with self.sample_lock:
            async with self.lock:
                sources = discover(self.hass)
                observations = []
                for binding in self.data["bindings"]:
                    value = observation(self.hass, binding, sources)
                    observations.append(value)
                    await self.policy.evaluate(
                        binding, value, self.authorization_reason(binding["bindingId"])
                    )
                await self.delivery.enqueue(observations)
            await self.delivery.drain()

    def authorization_reason(self, binding_id):
        """Bound offline authority; token rejection stops local rules immediately."""
        if not self.data.get("publisher") or self.delivery.data["revoked"]:
            return "publisher_authorization_required"
        state = self.delivery.data["bindings"].get(binding_id, {})
        if state.get("revoked"):
            return "binding_authorization_required"
        if time.time() >= state.get("authorizedUntil", 0):
            return "policy_authorization_expired"
        return None

    def snapshot(self):
        """Return no publisher credentials, hashes, token prefix or request bodies."""
        diagnostics = []
        if self.delivery.diagnostic:
            diagnostics.append(self.delivery.diagnostic)
        if (
            self.delivery.data["revoked"]
            and "publisher_authorization_required" not in diagnostics
        ):
            diagnostics.append("publisher_authorization_required")
        for binding in self.data["bindings"]:
            reason = self.authorization_reason(binding["bindingId"])
            if reason and (
                binding["policy"]["enabled"]
                or reason == "binding_authorization_required"
            ):
                diagnostics.append(f'{binding["bindingId"]}:{reason}')
        diagnostics.extend(
            f"{key}:{value}" for key, value in self.policy.diagnostics.items()
        )
        bindings = deepcopy(self.data["bindings"])
        for binding in bindings:
            for rule in binding["policy"]["rules"]:
                entity = self.resolve_action(f'{binding["bindingId"]}:{rule["id"]}')
                if entity:
                    rule["action"]["entityId"] = entity
        return {
            "protocol": 1,
            "publisherId": self.publisher_id,
            "instanceId": self.instance_id,
            "version": self.version,
            "revision": self.data["revision"],
            "configured": bool(self.data.get("publisher") and self.data["bindings"]),
            "sources": [source.public() for source in discover(self.hass)],
            "bindings": bindings,
            "diagnostics": diagnostics,
        }

    def resolve_action(self, key):
        """Resolve by registry ID so rename works and deleted/replaced targets stop."""
        identity = self.data["actions"].get(key)
        entry = er.async_get(self.hass).async_get(identity) if identity else None
        return entry.entity_id if entry else None

    def validate(self, bindings):
        """Require explicit, unique source selection and bounded local load targets."""
        bindings = vol.All([BINDING], vol.Length(max=32))(bindings)
        sources = {source.id: source for source in discover(self.hass)}
        ids, sites, source_ids, actions = set(), set(), set(), {}
        target_ids = {
            identity
            for publisher in self.owner.publishers.values()
            if publisher is not self
            for identity in publisher.data["actions"].values()
        }
        for binding in bindings:
            binding_id = binding["bindingId"]
            site = (binding["site"]["provider"], binding["site"]["id"])
            selected = binding["sourceIds"]
            if (
                binding_id in ids
                or site in sites
                or source_ids.intersection(selected)
                or len(set(selected)) != len(selected)
            ):
                raise vol.Invalid("Bindings, sites and inverter sources must be unique")
            ids.add(binding_id)
            sites.add(site)
            source_ids.update(selected)
            if any(key not in sources for key in selected):
                raise vol.Invalid("Select current official SolarEdge Modbus sources")
            meters = {meter.id for key in selected for meter in sources[key].meters}
            batteries = {
                battery.id for key in selected for battery in sources[key].batteries
            }
            if binding.get("gridMeterId") and binding["gridMeterId"] not in meters:
                raise vol.Invalid(
                    "Select a grid meter from the selected inverter group"
                )
            if len(set(binding["batteryIds"])) != len(binding["batteryIds"]) or not set(
                binding["batteryIds"]
            ).issubset(batteries):
                raise vol.Invalid("Select batteries from the selected inverter group")
            rule_ids = set()
            for rule in binding["policy"]["rules"]:
                if rule["id"] in rule_ids:
                    raise vol.Invalid("Rule IDs must be unique")
                rule_ids.add(rule["id"])
                entity = er.async_get(self.hass).async_get(rule["action"]["entityId"])
                if (
                    entity is None
                    or entity.disabled_by is not None
                    or entity.platform.startswith("solaredge")
                ):
                    raise vol.Invalid("Select a registered enabled HA load switch")
                if entity.id in target_ids:
                    raise vol.Invalid(
                        "A load switch may be controlled by only one energy rule"
                    )
                target_ids.add(entity.id)
                actions[f'{binding_id}:{rule["id"]}'] = entity.id
        return bindings, actions

    async def update(self, msg, operation):
        """Atomic optimistic writes and durable idempotency for lost WS replies."""
        if msg["instanceId"] != self.instance_id:
            raise vol.Invalid("Home Assistant identity changed")
        canonical = {
            key: value for key, value in msg.items() if key not in ("id", "type")
        }
        digest = hashlib.sha256(
            json.dumps({"operation": operation, **canonical}, sort_keys=True).encode()
        ).hexdigest()
        request_id = msg["operationId"]
        async with self.lock:
            previous = self.data["requests"].get(request_id)
            if previous:
                if previous != digest:
                    raise vol.Invalid(
                        "Operation ID was already used for a different request"
                    )
                await self.delivery.reconcile()
                return
            if msg["expectedRevision"] != self.data["revision"]:
                raise vol.Invalid("Energy settings changed. Refresh and try again")
            if operation == "unconfigure":
                bindings = [
                    item
                    for item in self.data["bindings"]
                    if item["bindingId"] != msg["bindingId"]
                ]
                if len(bindings) == len(self.data["bindings"]):
                    raise vol.Invalid("Energy binding does not exist")
                remaining = {item["bindingId"] for item in bindings}
                actions = {
                    key: value
                    for key, value in self.data["actions"].items()
                    if any(key.startswith(f"{binding_id}:") for binding_id in remaining)
                }
            else:
                bindings, actions = self.validate(msg["bindings"])
            updated = {
                **self.data,
                "bindings": bindings,
                "actions": actions,
                "revision": self.data["revision"] + 1,
            }
            if operation == "configure":
                updated["publisher"] = PUBLISHER(msg["publisher"])
                updated["authorizationRevision"] = updated["revision"]
                updated["configuredAt"] = time.time()
                if updated["publisher"]["bindingRevision"] != updated["revision"]:
                    raise vol.Invalid(
                        "Publisher binding revision must match the resulting revision"
                    )
            elif updated.get("publisher"):
                updated["publisher"] = {
                    **updated["publisher"],
                    "bindingRevision": updated["revision"],
                }
            if not bindings:
                updated.pop("publisher", None)
            # Saving is an explicit operator resume after manual override.
            updated["overrides"] = (
                {} if operation != "unconfigure" else self.data["overrides"]
            )
            if operation != "unconfigure":
                self.pending_overrides.clear()
            requests = {**self.data["requests"], request_id: digest}
            updated["requests"] = dict(list(requests.items())[-128:])
            await self.store.async_save(updated)
            self.data = updated
            self.policy.clear()
        await self.delivery.reconcile()


class EnergyRuntime:
    """Installation container with independent durable account publisher scopes."""

    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, "hombee.energy_publishers")
        self.publishers = {}
        self.lock = asyncio.Lock()
        self.instance_id = ""
        self.version = "unknown"

    async def load(self):
        self.instance_id = await instance_id.async_get(self.hass)
        integration = await async_get_integration(self.hass, "hombee")
        self.version = str(integration.version) if integration.version else "unknown"
        for publisher_id in await self.store.async_load() or []:
            publisher = PublisherRuntime(self.hass, self, publisher_id)
            await publisher.load()
            self.publishers[publisher_id] = publisher

    def snapshot(self, publisher_id=None):
        if publisher_id in self.publishers:
            return self.publishers[publisher_id].snapshot()
        result = {
            "protocol": 1,
            "instanceId": self.instance_id,
            "version": self.version,
            "revision": 0,
            "configured": False,
            "bindings": [],
            "diagnostics": [],
            "sources": [source.public() for source in discover(self.hass)],
        }
        if publisher_id is not None:
            result["publisherId"] = publisher_id
        return result

    async def update(self, msg, operation):
        # This lock covers cross-account target claims as well as index updates.
        async with self.lock:
            if msg["instanceId"] != self.instance_id:
                raise vol.Invalid("Home Assistant identity changed")
            publisher_id = msg["publisherId"]
            publisher = self.publishers.get(publisher_id)
            if publisher is None:
                if operation != "configure":
                    raise vol.Invalid("Publisher is not configured")
                publisher = PublisherRuntime(self.hass, self, publisher_id)
                # Persist the index before its scope so restart cannot lose an ACK.
                await self.store.async_save([*self.publishers, publisher_id])
                await publisher.load()
                self.publishers[publisher_id] = publisher
            await publisher.update(msg, operation)
