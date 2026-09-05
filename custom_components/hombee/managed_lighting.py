"""Managed logical lights backed by physical Home Assistant lights."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.light import brightness_supported
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .brightness import (
    ACTIVITIES,
    DEFAULT_PROFILE,
    BrightnessProfile,
    LuxController,
    finite_number,
)
from .const import (
    DEFAULT_COOL_KELVIN,
    DEFAULT_TRANSITION_SECONDS,
    DEFAULT_WARM_KELVIN,
    DOMAIN,
    LIGHTING_UPDATED,
    MANAGED_LIGHT_RECONCILE_INTERVAL,
)
from .light import HombeeManagedLight
from .lighting_configuration import (
    configuration_change,
    light_settings,
    validate_light_settings,
)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.managed_lighting"
_LOGGER = logging.getLogger(__name__)


class ManagedLightingError(ValueError):
    """Raised when a physical light cannot be managed safely."""


@dataclass(frozen=True, slots=True)
class ManagedLightSpec:
    """Desired policy for one public logical light."""

    source_entity_id: str
    warm_kelvin: int = DEFAULT_WARM_KELVIN
    cool_kelvin: int = DEFAULT_COOL_KELVIN
    transition_seconds: float = DEFAULT_TRANSITION_SECONDS


@dataclass(frozen=True, slots=True)
class ManagedLightMapping:
    """Persistent link between one logical light and its physical target."""

    source_registry_id: str
    public_entity_id: str
    physical_entity_id: str
    logical_unique_id: str
    name: str
    warm_kelvin: int
    cool_kelvin: int
    transition_seconds: float
    original_hidden_by: str | None = None
    area_id: str | None = None
    device_id: str | None = None
    icon: str | None = None
    labels: tuple[str, ...] = ()
    manual_override: bool = False
    brightness_override: bool = False
    adapt_brightness: bool = True
    adapt_color: bool = True
    min_brightness: float = 1
    max_brightness: float = 100

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ManagedLightMapping:
        """Restores a mapping from integration storage."""
        return cls(
            source_registry_id=str(raw["source_registry_id"]),
            public_entity_id=str(raw["public_entity_id"]),
            physical_entity_id=str(raw["physical_entity_id"]),
            logical_unique_id=str(raw["logical_unique_id"]),
            name=str(raw["name"]),
            warm_kelvin=int(raw["warm_kelvin"]),
            cool_kelvin=int(raw["cool_kelvin"]),
            transition_seconds=float(raw["transition_seconds"]),
            original_hidden_by=(
                str(raw["original_hidden_by"])
                if raw.get("original_hidden_by") is not None
                else None
            ),
            area_id=str(raw["area_id"]) if raw.get("area_id") else None,
            device_id=str(raw["device_id"]) if raw.get("device_id") else None,
            icon=str(raw["icon"]) if raw.get("icon") else None,
            labels=tuple(str(label) for label in raw.get("labels", [])),
            manual_override=bool(raw.get("manual_override", False)),
            brightness_override=bool(raw.get("brightness_override", False)),
            adapt_brightness=bool(raw.get("adapt_brightness", True)),
            adapt_color=bool(raw.get("adapt_color", True)),
            min_brightness=float(raw.get("min_brightness", 1)),
            max_brightness=float(raw.get("max_brightness", 100)),
        )


def circadian_kelvin(
    *,
    now: datetime,
    above_horizon: bool,
    rising: bool,
    next_rising: datetime | None,
    next_noon: datetime | None,
    next_setting: datetime | None,
    warm_kelvin: int,
    cool_kelvin: int,
) -> int:
    """Calculates the shared Hombee color-temperature curve."""
    warm = min(warm_kelvin, cool_kelvin)
    cool = max(warm_kelvin, cool_kelvin)
    if not above_horizon:
        return warm

    current = now.timestamp()
    if rising:
        start = (next_rising or now + timedelta(days=1)).timestamp() - 86400
        finish = (next_noon or now + timedelta(hours=12)).timestamp()
        phase = (current - start) / max(finish - start, 1)
    else:
        start = (next_noon or now + timedelta(hours=12)).timestamp() - 86400
        finish = (next_setting or now + timedelta(days=1)).timestamp()
        phase = (finish - current) / max(finish - start, 1)

    bounded = min(max(phase, 0), 1)
    eased = bounded * bounded * (3 - 2 * bounded)
    return round(warm + (cool - warm) * eased)


class ManagedLightingManager:
    """Owns persistent logical-light mappings for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.enabled = True
        self.brightness_enabled = True
        self.profiles = {DEFAULT_PROFILE: BrightnessProfile()}
        self.activities: dict[str, str] = {}
        self.lux_controllers: dict[str, LuxController] = {}
        self._room_settled_after: dict[str, datetime] = {}
        self._reconcile_lock = asyncio.Lock()
        self.configuration_lock = asyncio.Lock()
        self._discovery_lock = asyncio.Lock()
        self._cancel_discovery: Callable[[], None] | None = None
        self.mappings: dict[str, ManagedLightMapping] = {}
        self.entities: dict[str, Any] = {}
        self._add_entities: AddEntitiesCallback | None = None
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )
        self._cancel_interval: Callable[[], None] | None = None

    async def async_load(self) -> None:
        """Load mappings and start periodic lighting reconciliation."""
        raw = await self._store.async_load() or {}
        self.enabled = bool(raw.get("enabled", True))
        self.brightness_enabled = bool(raw.get("brightness_enabled", True))
        for key, value in raw.get("profiles", {}).items():
            try:
                self.profiles[key] = BrightnessProfile.from_dict(value)
            except vol.Invalid:
                _LOGGER.warning("Ignoring invalid lighting profile %s", key)
        self.activities = {
            key: value
            for key, value in raw.get("activities", {}).items()
            if value in ACTIVITIES
        }
        for item in raw.get("lights", []):
            if isinstance(item, Mapping):
                mapping = ManagedLightMapping.from_dict(item)
                self.mappings[mapping.source_registry_id] = mapping
        self._cancel_interval = async_track_time_interval(
            self.hass,
            self._async_reconcile_temperatures,
            MANAGED_LIGHT_RECONCILE_INTERVAL,
            name=f"{DOMAIN} managed light reconciliation",
        )

    async def async_unload(self) -> None:
        """Stops manager-owned callbacks."""
        if self._cancel_discovery is not None:
            self._cancel_discovery()
            self._cancel_discovery = None
        if self._cancel_interval is not None:
            self._cancel_interval()
            self._cancel_interval = None
        self._add_entities = None
        await self._async_save()

    async def async_setup_platform(
        self, async_add_entities: AddEntitiesCallback
    ) -> None:
        """Connects the light platform and restores persisted entities."""
        self._add_entities = async_add_entities
        registry = er.async_get(self.hass)
        for mapping in tuple(self.mappings.values()):
            if _entry_by_registry_id(registry, mapping.source_registry_id) is None:
                await self._async_remove_mapping(mapping)
            else:
                await self._async_activate_mapping(mapping)
        self._cancel_discovery = self.hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._source_added
        )
        self.entry.async_on_unload(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated
            )
        )
        await self.async_discover()

    @callback
    def _registry_updated(self, event: Event) -> None:
        """Keep public metadata and room selectors current after edits in HA."""
        if event.data.get("action") != "update":
            return
        entity_id = event.data.get("entity_id")
        registry_entry = er.async_get(self.hass).async_get(entity_id)
        if registry_entry is None or registry_entry.platform != DOMAIN:
            return
        for mapping in tuple(self.mappings.values()):
            if mapping.logical_unique_id != registry_entry.unique_id:
                continue
            self._update_mapping(
                replace(
                    mapping,
                    public_entity_id=registry_entry.entity_id,
                    area_id=registry_entry.area_id,
                    device_id=registry_entry.device_id,
                    labels=tuple(sorted(registry_entry.labels)),
                    icon=registry_entry.icon,
                    name=registry_entry.name or mapping.name,
                )
            )
            self.lux_controllers.clear()
            async_dispatcher_send(self.hass, LIGHTING_UPDATED)
            break

    @callback
    def _source_added(self, event: Event) -> None:
        entity_id = event.data.get("entity_id", "")
        if (
            entity_id.startswith("light.")
            and (state := event.data.get("new_state")) is not None
            and brightness_supported(state.attributes.get("supported_color_modes"))
            and self._mapping_for_requested_entity(entity_id) is None
        ):
            self.entry.async_create_background_task(
                self.hass, self.async_discover(), "Discover Hombee lights"
            )

    async def async_discover(self) -> None:
        """Wrap registered dimmable lights as they become available."""
        async with self._discovery_lock:
            registry = er.async_get(self.hass)
            for source in tuple(registry.entities.values()):
                if (
                    source.domain != LIGHT_DOMAIN
                    or source.platform in {DOMAIN, "group"}
                    or source.disabled_by is not None
                    or source.id in self.mappings
                ):
                    continue
                state = self.hass.states.get(source.entity_id)
                if state is None or not brightness_supported(
                    state.attributes.get("supported_color_modes")
                ):
                    continue
                try:
                    mapping = await self._async_prepare_mapping(
                        ManagedLightSpec(source_entity_id=source.entity_id)
                    )
                    await self._async_activate_mapping(mapping)
                except ManagedLightingError, HomeAssistantError:
                    _LOGGER.exception("Cannot manage light %s", source.entity_id)
            await self._async_save()
            async_dispatcher_send(self.hass, LIGHTING_UPDATED)

    async def async_set_enabled(self, enabled: bool) -> None:
        """Persist the global policy, shared by all managed lights."""
        await self.async_configure_enabled(color_enabled=enabled)

    def current_kelvin(self, mapping: ManagedLightMapping) -> int:
        """Calculates and clamps the requested temperature for a mapping."""
        sun = self.hass.states.get("sun.sun")
        now = dt_util.now()
        requested = circadian_kelvin(
            now=now,
            above_horizon=sun is not None and sun.state == "above_horizon",
            rising=bool(sun.attributes.get("rising", False)) if sun else False,
            next_rising=_datetime_attribute(sun, "next_rising"),
            next_noon=_datetime_attribute(sun, "next_noon"),
            next_setting=_datetime_attribute(sun, "next_setting"),
            warm_kelvin=mapping.warm_kelvin,
            cool_kelvin=mapping.cool_kelvin,
        )
        source = self.hass.states.get(mapping.physical_entity_id)
        if source is None:
            return requested
        minimum, maximum = _kelvin_range(source, requested)
        return min(max(requested, minimum), maximum)

    @callback
    def set_manual_override(self, source_registry_id: str, active: bool) -> None:
        """Persists a user-selected color mode until the light turns off."""
        mapping = self.mappings[source_registry_id]
        if mapping.manual_override == active:
            return
        self._update_mapping(replace(mapping, manual_override=active))

    @callback
    def set_brightness_override(self, source_registry_id: str, active: bool) -> None:
        """Pause brightness independently of automatic color temperature."""
        mapping = self.mappings[source_registry_id]
        if mapping.brightness_override != active:
            self._update_mapping(replace(mapping, brightness_override=active))
            self.lux_controllers.pop(self.room_key(mapping), None)

    @callback
    def _update_mapping(self, updated: ManagedLightMapping) -> None:
        source_registry_id = updated.source_registry_id
        self.mappings[source_registry_id] = updated
        entity = self.entities.get(source_registry_id)
        if entity is not None:
            entity.mapping = updated
        self._store.async_delay_save(self._storage_data, 1)

    def room_key(self, mapping: ManagedLightMapping) -> str:
        """Use the current public area assignment, including edits made in HA."""
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            LIGHT_DOMAIN, DOMAIN, mapping.logical_unique_id
        )
        entry = registry.async_get(entity_id) if entity_id else None
        area_id = entry.area_id if entry else mapping.area_id
        if area_id is None and entry and entry.device_id:
            device = dr.async_get(self.hass).async_get(entry.device_id)
            area_id = device.area_id if device else None
        return area_id or f"light:{mapping.source_registry_id}"

    def profile_for(self, mapping: ManagedLightMapping) -> BrightnessProfile:
        return self.profiles.get(self.room_key(mapping), self.profiles[DEFAULT_PROFILE])

    def activity_for(self, mapping: ManagedLightMapping) -> str:
        return self.activities.get(
            self.room_key(mapping), self.activities.get(DEFAULT_PROFILE, "auto")
        )

    def brightness_target(self, mapping: ManagedLightMapping) -> int:
        """Return a nonzero HA brightness without advancing the feedback loop."""
        profile = self.profile_for(mapping)
        percent, _ = profile.targets(dt_util.now(), self.activity_for(mapping))
        controller = self.lux_controllers.get(self.room_key(mapping))
        if (
            self._lux_sample(profile) is not None
            and controller is not None
            and controller.output is not None
        ):
            percent = controller.output
        percent = max(mapping.min_brightness, min(mapping.max_brightness, percent))
        return max(1, round(percent * 255 / 100))

    def brightness_status(self, mapping: ManagedLightMapping) -> dict[str, Any]:
        """Expose the decision and manual state on the public light."""
        profile = self.profile_for(mapping)
        controller = self.lux_controllers.get(self.room_key(mapping))
        using_lux = (
            self._lux_sample(profile) is not None
            and controller is not None
            and controller.output is not None
        )
        return {
            "brightness_control": (
                "disabled"
                if not self.brightness_enabled or not mapping.adapt_brightness
                else (
                    "manual"
                    if mapping.brightness_override
                    else "lux" if using_lux else "schedule"
                )
            ),
            "color_manual_override": mapping.manual_override,
            "brightness_manual_override": mapping.brightness_override,
            "lighting_activity": self.activity_for(mapping),
            "target_brightness": self.brightness_target(mapping),
            "target_illuminance": profile.targets(
                dt_util.now(), self.activity_for(mapping)
            )[1],
            "illuminance_sensor": profile.illuminance_sensor or None,
        }

    def _lux_sample(self, profile: BrightnessProfile) -> tuple[float, datetime] | None:
        state = self.hass.states.get(profile.illuminance_sensor)
        if state is None or state.attributes.get("unit_of_measurement") != "lx":
            return None
        try:
            measured = finite_number(state.state)
        except vol.Invalid:
            return None
        age = (dt_util.utcnow() - state.last_reported).total_seconds()
        if measured < 0 or not 0 <= age <= profile.sensor_max_age:
            return None
        return measured, state.last_reported

    def _update_lux_targets(self) -> None:
        rooms: dict[str, list[HombeeManagedLight]] = {}
        for entity in self.entities.values():
            if entity.is_on and entity.automatic_brightness:
                rooms.setdefault(self.room_key(entity.mapping), []).append(entity)
        for key in set(self.lux_controllers) - rooms.keys():
            self.lux_controllers.pop(key, None)
        for key, entities in rooms.items():
            profile = self.profile_for(entities[0].mapping)
            sample = self._lux_sample(profile)
            if sample is None:
                self.lux_controllers.pop(key, None)
                continue
            active = [entity for entity in entities if entity.brightness is not None]
            if not active:
                continue
            current = sum(entity.brightness * 100 / 255 for entity in active) / len(
                active
            )
            controller = self.lux_controllers.setdefault(
                key, LuxController(settled_after=self._room_settled_after.get(key))
            )
            controller.update(
                measured=sample[0],
                reported=sample[1],
                target=profile.targets(
                    dt_util.now(), self.activity_for(active[0].mapping)
                )[1],
                current=current,
                minimum=min(entity.mapping.min_brightness for entity in active),
                maximum=max(entity.mapping.max_brightness for entity in active),
            )

    @callback
    def light_changed(
        self, mapping: ManagedLightMapping, transition: float = 0
    ) -> None:
        """Wait for a measurement taken after the lamps have settled."""
        key = self.room_key(mapping)
        settled_after = dt_util.utcnow() + timedelta(seconds=max(10, transition))
        self._room_settled_after[key] = settled_after
        controller = self.lux_controllers.get(key)
        if controller is not None:
            controller.settled_after = settled_after

    async def async_set_brightness_enabled(self, enabled: bool) -> None:
        await self.async_configure_enabled(brightness_enabled=enabled)

    @configuration_change
    async def async_configure_enabled(
        self,
        *,
        brightness_enabled: bool | None = None,
        color_enabled: bool | None = None,
    ) -> None:
        """Change both global switches in a single saved configuration."""
        if brightness_enabled is None and color_enabled is None:
            raise vol.Invalid("Choose a brightness or color policy")
        if brightness_enabled is not None:
            self.brightness_enabled = brightness_enabled
        if color_enabled is not None:
            self.enabled = color_enabled
        self.lux_controllers.clear()
        for source_id in self.mappings:
            if brightness_enabled:
                self.set_brightness_override(source_id, False)
            if color_enabled:
                self.set_manual_override(source_id, False)
        await self._async_save()
        async_dispatcher_send(self.hass, LIGHTING_UPDATED)
        await self._async_reconcile_temperatures(dt_util.now(), force=True)

    @configuration_change
    async def async_configure_profile(
        self, key: str, data: dict[str, Any], *, patch: bool = False
    ) -> None:
        """Persist a validated default or room profile."""
        self.validate_profile_key(key)
        if patch:
            existing = self.profiles.get(key, self.profiles[DEFAULT_PROFILE]).to_dict()
            data = {**existing, **data}
        profile = BrightnessProfile.from_dict(data)
        if key == DEFAULT_PROFILE and profile.illuminance_sensor:
            raise vol.Invalid("Assign illuminance sensors to individual rooms")
        if profile.illuminance_sensor:
            state = self.hass.states.get(profile.illuminance_sensor)
            registry_entry = er.async_get(self.hass).async_get(
                profile.illuminance_sensor
            )
            if (
                not profile.illuminance_sensor.startswith("sensor.")
                or state is None
                or state.attributes.get("device_class") != "illuminance"
                or (
                    registry_entry is not None
                    and registry_entry.disabled_by is not None
                )
            ):
                raise vol.Invalid("Choose an enabled illuminance sensor")
        self.profiles[key] = profile
        self.lux_controllers.clear()
        await self._async_save()
        async_dispatcher_send(self.hass, LIGHTING_UPDATED)
        await self._async_reconcile_temperatures(dt_util.now(), force=True)

    @configuration_change
    async def async_configure_light(self, source_id: str, data: dict[str, Any]) -> None:
        """Save independently enabled attributes and automatic brightness limits."""
        mapping = self.mappings[source_id]
        data = validate_light_settings({**light_settings(mapping), **data})
        self._update_mapping(replace(mapping, **data))
        self.lux_controllers.clear()
        await self._async_save()
        await self._async_reconcile_temperatures(dt_util.now(), force=True)

    @configuration_change
    async def async_reset_profile(self, key: str) -> None:
        """Return a room to the default profile and inherited activity."""
        self.validate_profile_key(key)
        if key != DEFAULT_PROFILE:
            self.profiles.pop(key, None)
            self.activities.pop(key, None)
            self.lux_controllers.clear()
            await self._async_save()
            async_dispatcher_send(self.hass, LIGHTING_UPDATED)
            await self._async_reconcile_temperatures(dt_util.now(), force=True)
        else:
            raise vol.Invalid("The default profile cannot be removed")

    @configuration_change
    async def async_set_activity(self, key: str, activity: str) -> None:
        """Change a room target without overriding a person's manual lamp setting."""
        self.validate_profile_key(key)
        if activity == "inherit" and key != DEFAULT_PROFILE:
            self.activities.pop(key, None)
        elif activity in ACTIVITIES:
            self.activities[key] = activity
        else:
            raise vol.Invalid(f"Unknown lighting activity: {activity}")
        self.lux_controllers.clear()
        await self._async_save()
        async_dispatcher_send(self.hass, LIGHTING_UPDATED)
        await self._async_reconcile_temperatures(dt_util.now(), force=True)

    def validate_profile_key(self, key: str) -> None:
        """Reject misspelled areas instead of storing unreachable profiles."""
        if (
            key != DEFAULT_PROFILE
            and ar.async_get(self.hass).async_get_area(key) is None
        ):
            raise vol.Invalid("Unknown lighting area")

    @configuration_change
    async def async_resume_lights(
        self, entity_ids: list[str], *, brightness: bool = True, color: bool = True
    ) -> None:
        """Validate every target before clearing any manual override."""
        mappings = []
        for entity_id in entity_ids:
            mapping = self._mapping_for_requested_entity(entity_id)
            if mapping is None or mapping.public_entity_id != entity_id:
                raise vol.Invalid(f"Unknown managed public light: {entity_id}")
            mappings.append(mapping)
        for mapping in mappings:
            if brightness:
                self.set_brightness_override(mapping.source_registry_id, False)
            if color:
                self.set_manual_override(mapping.source_registry_id, False)
        await self._async_save()
        await self._async_reconcile_temperatures(dt_util.now(), force=True)

    async def _async_prepare_mapping(
        self, spec: ManagedLightSpec
    ) -> ManagedLightMapping:
        registry = er.async_get(self.hass)
        source_entry = registry.async_get(spec.source_entity_id)
        if source_entry is None:
            raise ManagedLightingError(
                f"Physical light {spec.source_entity_id} is not in the entity registry"
            )
        if source_entry.platform == DOMAIN:
            raise ManagedLightingError(
                f"{spec.source_entity_id} is already a Hombee logical light"
            )
        source_state = self.hass.states.get(spec.source_entity_id)
        if source_state is None or not brightness_supported(
            source_state.attributes.get("supported_color_modes")
        ):
            raise ManagedLightingError(
                f"Physical light {spec.source_entity_id} does not support " "brightness"
            )

        public_entity_id = spec.source_entity_id
        object_id = public_entity_id.split(".", 1)[1]
        physical_entity_id = registry.async_get_available_entity_id(
            LIGHT_DOMAIN, f"{object_id}_physical"
        )
        # Capture device metadata too: the logical entity's platform registration
        # can clear its device link, so inherited assignments must be explicit.
        area_id = source_entry.area_id
        labels = set(source_entry.labels)
        if source_entry.device_id is not None and (
            device := dr.async_get(self.hass).async_get(source_entry.device_id)
        ):
            if area_id is None:
                area_id = device.area_id
            labels.update(device.labels)

        mapping = ManagedLightMapping(
            source_registry_id=source_entry.id,
            public_entity_id=public_entity_id,
            physical_entity_id=physical_entity_id,
            logical_unique_id=f"managed_light_{source_entry.id}",
            name=str(source_state.attributes.get("friendly_name", object_id)),
            warm_kelvin=spec.warm_kelvin,
            cool_kelvin=spec.cool_kelvin,
            transition_seconds=spec.transition_seconds,
            original_hidden_by=(
                source_entry.hidden_by.value
                if source_entry.hidden_by is not None
                else None
            ),
            area_id=area_id,
            device_id=source_entry.device_id,
            icon=source_entry.icon,
            labels=tuple(sorted(labels)),
        )
        self.mappings[mapping.source_registry_id] = mapping
        await self._async_save()
        return mapping

    async def _async_activate_mapping(self, mapping: ManagedLightMapping) -> None:
        registry = er.async_get(self.hass)
        source_entry = _entry_by_registry_id(registry, mapping.source_registry_id)
        if source_entry is None:
            raise ManagedLightingError(
                "Physical entity registry entry "
                f"{mapping.source_registry_id} is missing"
            )

        if source_entry.entity_id == mapping.public_entity_id:
            registry.async_update_entity(
                source_entry.entity_id,
                new_entity_id=mapping.physical_entity_id,
                hidden_by=er.RegistryEntryHider.INTEGRATION,
            )
            await self.hass.async_block_till_done()
        elif source_entry.entity_id != mapping.physical_entity_id:
            mapping = replace(mapping, physical_entity_id=source_entry.entity_id)
            self.mappings[mapping.source_registry_id] = mapping
        elif source_entry.hidden_by is None:
            registry.async_update_entity(
                source_entry.entity_id,
                hidden_by=er.RegistryEntryHider.INTEGRATION,
            )

        logical_entry = registry.async_get_or_create(
            LIGHT_DOMAIN,
            DOMAIN,
            mapping.logical_unique_id,
            suggested_object_id=mapping.public_entity_id.split(".", 1)[1],
            config_entry=self.entry,
        )
        registry.async_update_entity(
            logical_entry.entity_id,
            area_id=mapping.area_id,
            device_id=mapping.device_id,
            icon=mapping.icon,
            labels=set(mapping.labels),
            name=mapping.name,
        )
        if logical_entry.entity_id != mapping.public_entity_id:
            if registry.async_get(mapping.public_entity_id) is not None:
                raise ManagedLightingError(
                    f"Public entity id {mapping.public_entity_id} is not available"
                )
            registry.async_update_entity(
                logical_entry.entity_id,
                new_entity_id=mapping.public_entity_id,
            )

        if mapping.source_registry_id in self.entities:
            self.entities[mapping.source_registry_id].mapping = mapping
            return
        if self._add_entities is None:
            return

        entity = HombeeManagedLight(self, mapping)
        self.entities[mapping.source_registry_id] = entity
        self._add_entities([entity])
        await self.hass.async_block_till_done()

    async def async_remove(self) -> None:
        """Restore physical entity IDs when the integration is removed."""
        await self.async_unload()
        for mapping in tuple(self.mappings.values()):
            await self._async_remove_mapping(mapping)
        await self._store.async_remove()

    async def _async_remove_mapping(self, mapping: ManagedLightMapping) -> None:
        registry = er.async_get(self.hass)
        entity = self.entities.pop(mapping.source_registry_id, None)
        if entity is not None:
            await entity.async_remove()

        logical_entity_id = registry.async_get_entity_id(
            LIGHT_DOMAIN, DOMAIN, mapping.logical_unique_id
        )
        if logical_entity_id is not None:
            registry.async_remove(logical_entity_id)

        source_entry = _entry_by_registry_id(registry, mapping.source_registry_id)
        if source_entry is not None:
            if registry.async_get(mapping.public_entity_id) is not None:
                raise ManagedLightingError(
                    f"Cannot restore {mapping.public_entity_id}; the id is occupied"
                )
            hidden_by = (
                er.RegistryEntryHider(mapping.original_hidden_by)
                if mapping.original_hidden_by is not None
                else None
            )
            registry.async_update_entity(
                source_entry.entity_id,
                new_entity_id=mapping.public_entity_id,
                hidden_by=hidden_by,
            )
            await self.hass.async_block_till_done()
        self.mappings.pop(mapping.source_registry_id, None)

    async def _async_reconcile_temperatures(
        self, _now: datetime, *, force: bool = False
    ) -> None:
        async with self._reconcile_lock:
            self._update_lux_targets()
            for entity in tuple(self.entities.values()):
                try:
                    await entity.async_reconcile_temperature(force=force)
                except HomeAssistantError:
                    _LOGGER.exception("Cannot update light %s", entity.entity_id)

    def _mapping_for_requested_entity(
        self, entity_id: str
    ) -> ManagedLightMapping | None:
        for mapping in self.mappings.values():
            if entity_id in {mapping.public_entity_id, mapping.physical_entity_id}:
                return mapping
        return None

    async def _async_save(self) -> None:
        await self._store.async_save(self._storage_data())

    def _storage_data(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "brightness_enabled": self.brightness_enabled,
            "profiles": {key: value.to_dict() for key, value in self.profiles.items()},
            "activities": self.activities,
            "lights": [asdict(mapping) for mapping in self.mappings.values()],
        }


def _entry_by_registry_id(
    registry: er.EntityRegistry, registry_id: str
) -> er.RegistryEntry | None:
    return next(
        (entry for entry in registry.entities.values() if entry.id == registry_id),
        None,
    )


def _datetime_attribute(state: State | None, key: str) -> datetime | None:
    if state is None:
        return None
    value = state.attributes.get(key)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return dt_util.parse_datetime(value)
    return None


def _kelvin_range(state: State, fallback: int) -> tuple[int, int]:
    attributes = state.attributes
    minimum = attributes.get("min_color_temp_kelvin")
    maximum = attributes.get("max_color_temp_kelvin")
    if isinstance(minimum, (int, float)) and isinstance(maximum, (int, float)):
        return round(minimum), round(maximum)

    min_mireds = attributes.get("min_mireds")
    max_mireds = attributes.get("max_mireds")
    if isinstance(min_mireds, (int, float)) and isinstance(max_mireds, (int, float)):
        return round(1_000_000 / max_mireds), round(1_000_000 / min_mireds)
    return fallback, fallback
