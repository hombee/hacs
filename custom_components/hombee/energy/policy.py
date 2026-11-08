"""Local opt-in switch actions with freshness, dwell, overrides, and readback."""

from __future__ import annotations

import asyncio
import time

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er


class PolicyEngine:
    """No outbox inputs, restart replay, inverter writes, or arbitrary services."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.since = {}
        self.active = set()
        self.contexts = set()
        self.diagnostics = {}

    def clear(self):
        self.since.clear()
        self.active.clear()
        self.diagnostics.clear()

    async def changed(self, event):
        """External target changes pause that rule durably until an explicit save."""
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (
            old is None
            or new is None
            or old.state == new.state
            or event.context.id in self.contexts
        ):
            return
        changed = False
        for binding in self.runtime.data["bindings"]:
            for rule in binding["policy"]["rules"]:
                key = f'{binding["bindingId"]}:{rule["id"]}'
                target = self.runtime.resolve_action(key)
                if (
                    new.entity_id == target
                    and old.state in ("on", "off")
                    and new.state in ("on", "off")
                ):
                    self.runtime.data["overrides"][key] = True
                    changed = True
        if changed:
            await self.runtime.store.async_save(self.runtime.data)

    async def evaluate(  # noqa: PLR0912, PLR0915
        self, binding, value, authorization_reason=None
    ):
        """Evaluate only a current coherent sample, once per threshold crossing."""
        policy = binding["policy"]
        for rule in policy["rules"]:
            now = time.time()
            key = f'{binding["bindingId"]}:{rule["id"]}'
            reason = (
                self.runtime.authorization_reason(binding["bindingId"])
                or authorization_reason
            )
            if not policy["enabled"] or not rule["enabled"]:
                reason = "disabled"
            elif reason is not None:
                pass
            elif (
                key in self.runtime.pending_overrides
                or self.runtime.data["overrides"].get(key)
                or now * 1000 < rule.get("manualOverrideUntilMs", 0)
            ):
                reason = "manual_override"
            elif (
                value["quality"] != "fresh"
                or not 0
                <= now * 1000 - value["observedAtMs"]
                <= policy["maxObservationAgeSeconds"] * 1000
            ):
                reason = "inputs_not_fresh"
            matched = reason is None
            for condition in rule["conditions"]:
                actual = value["telemetry"].get(condition["field"])
                if condition["field"] == "gridState":
                    actual = {"connected": 1, "disconnected": 0}.get(value["gridState"])
                margin = condition["hysteresis"] if key in self.active else 0
                matched = (
                    matched
                    and actual is not None
                    and (
                        actual >= condition.get("minimum", float("-inf")) - margin
                        and actual <= condition.get("maximum", float("inf")) + margin
                    )
                )
            if not matched:
                self.since.pop(key, None)
                self.active.discard(key)
                self.diagnostics[key] = reason or "conditions_not_met"
                continue
            self.since.setdefault(key, now)
            if key in self.active or now - self.since[key] < rule["dwellSeconds"]:
                self.diagnostics[key] = "holding" if key in self.active else "dwelling"
                continue
            entity_id = self.runtime.resolve_action(key)
            state = self.runtime.hass.states.get(entity_id) if entity_id else None
            entry = (
                er.async_get(self.runtime.hass).async_get(entity_id)
                if entity_id
                else None
            )
            if (
                state is None
                or state.state not in ("on", "off")
                or entry is None
                or entry.disabled_by is not None
                or entry.platform.startswith("solaredge")
            ):
                self.diagnostics[key] = "target_unavailable"
                continue
            desired = "on" if rule["action"]["service"] == "turnOn" else "off"
            self.active.add(key)
            if state.state == desired:
                self.diagnostics[key] = "already_satisfied"
                continue
            if self.runtime.authorization_reason(binding["bindingId"]) is not None:
                self.active.discard(key)
                self.since.pop(key, None)
                self.diagnostics[key] = self.runtime.authorization_reason(
                    binding["bindingId"]
                )
                continue
            if key in self.runtime.pending_overrides:
                self.active.discard(key)
                self.since.pop(key, None)
                self.diagnostics[key] = "manual_override"
                continue
            if (
                not 0
                <= time.time() * 1000 - value["observedAtMs"]
                <= policy["maxObservationAgeSeconds"] * 1000
            ):
                self.active.discard(key)
                self.since.pop(key, None)
                self.diagnostics[key] = "inputs_not_fresh"
                continue
            context = Context()
            self.contexts.add(context.id)
            # Keep contexts bounded while allowing delayed state reports to match.
            if len(self.contexts) > 128:
                self.contexts = {context.id}
            try:
                async with asyncio.timeout(5):
                    await self.runtime.hass.services.async_call(
                        "switch",
                        f"turn_{desired}",
                        {"entity_id": entity_id},
                        blocking=True,
                        context=context,
                    )
                readback = self.runtime.hass.states.get(entity_id)
                if readback is None or readback.state != desired:
                    raise HomeAssistantError("Action readback not confirmed")
                self.diagnostics[key] = "applied"
            except HomeAssistantError, TimeoutError:
                # An uncertain outcome must never cause a repeated load command.
                self.runtime.data["overrides"][key] = True
                await self.runtime.store.async_save(self.runtime.data)
                self.diagnostics[key] = "readback_unconfirmed"
