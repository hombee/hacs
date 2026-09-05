"""Climate entity for the Hombee Air unit.

The unit's programs map to climate presets (off, economy, comfort, comfort+,
manual). Home Assistant only allows built-in HVAC modes, so Hombee Air exposes
its user-facing program choice through presets and keeps HVAC mode as the
internal auto/off operating state. Setpoint changes write the active program's
register pair, mirroring how the unit resolves its own setpoints.
"""

from __future__ import annotations

from time import monotonic
from typing import Any, ClassVar

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    KEY_COOLING_DEMAND,
    KEY_CURRENT_FAN_GEAR,
    KEY_CURRENT_HUMIDITY_SETPOINT,
    KEY_CURRENT_PROGRAM,
    KEY_CURRENT_TEMPERATURE_SETPOINT,
    KEY_DRYING_DEMAND,
    KEY_FANS_RUNNING,
    KEY_HEATING_DEMAND,
    KEY_PROGRAM_MODE,
    KEY_ROOM_HUMIDITY,
    KEY_ROOM_TEMPERATURE,
    OPTIMISTIC_HOLD_SECONDS,
    PRESET_COMFORT,
    PRESET_OFF,
    PRESET_SETPOINTS,
    PRESET_TO_PROGRAM,
    PROGRAM_OFF,
    PROGRAM_TO_PRESET,
    PresetSetpoints,
)
from .coordinator import (
    HombeeAirConfigEntry,
    HombeeAirCoordinator,
    HombeeAirRuntime,
)
from .entity import device_info
from .modbus_client import HombeeAirModbusError
from .registers import REGISTERS_BY_KEY, HombeeAirRegister

_FAN_MODES = ["1", "2", "3"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HombeeAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up the climate entity for one Hombee Air unit."""
    async_add_entities([HombeeAirClimate(entry.runtime_data, entry.title)])


class HombeeAirClimate(CoordinatorEntity[HombeeAirCoordinator], ClimateEntity):
    """Native climate device backed by the unit's Modbus registers."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.1
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0
    _attr_min_humidity = 0
    _attr_max_humidity = 100
    _attr_hvac_modes: ClassVar[list[HVACMode]] = [HVACMode.AUTO]
    _attr_fan_modes: ClassVar[list[str]] = _FAN_MODES
    _attr_preset_modes: ClassVar[list[str]] = list(PRESET_TO_PROGRAM)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_HUMIDITY
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, runtime: HombeeAirRuntime, title: str) -> None:
        super().__init__(runtime.fast)
        self._runtime = runtime
        self._optimistic: dict[str, int] = {}
        self._optimistic_deadlines: dict[str, float] = {}
        self._last_active_program = PRESET_TO_PROGRAM[PRESET_COMFORT]
        self._attr_unique_id = f"{DOMAIN}_{runtime.slug}_climate"
        self._attr_device_info = device_info(runtime.slug, title)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.slow.async_add_listener(self._handle_coordinator_update)
        )

    @property
    def available(self) -> bool:
        return (
            self._runtime.fast.last_update_success
            and self._runtime.slow.last_update_success
        )

    @property
    def current_temperature(self) -> float | None:
        return self._numeric(KEY_ROOM_TEMPERATURE)

    @property
    def current_humidity(self) -> float | None:
        return self._numeric(KEY_ROOM_HUMIDITY)

    @property
    def target_temperature(self) -> float | None:
        return self._numeric(KEY_CURRENT_TEMPERATURE_SETPOINT)

    @property
    def target_humidity(self) -> float | None:
        return self._numeric(KEY_CURRENT_HUMIDITY_SETPOINT)

    @property
    def hvac_mode(self) -> HVACMode | None:
        program = self._program()
        if program is None:
            return None
        return HVACMode.OFF if program == PROGRAM_OFF else HVACMode.AUTO

    @property
    def hvac_action(self) -> HVACAction | None:
        program = self._program()
        if program is None:
            return None
        if program == PROGRAM_OFF:
            return HVACAction.OFF
        demand_actions = (
            (KEY_HEATING_DEMAND, HVACAction.HEATING),
            (KEY_COOLING_DEMAND, HVACAction.COOLING),
            (KEY_DRYING_DEMAND, HVACAction.DRYING),
            (KEY_FANS_RUNNING, HVACAction.FAN),
        )
        for key, action in demand_actions:
            if self._flag(key):
                return action
        return HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        program = self._program()
        if program in PROGRAM_TO_PRESET:
            return PROGRAM_TO_PRESET[program]
        resolved = self._raw(KEY_CURRENT_PROGRAM)
        if isinstance(resolved, int) and resolved in PROGRAM_TO_PRESET:
            return PROGRAM_TO_PRESET[resolved]
        return None

    @property
    def fan_mode(self) -> str | None:
        gear = self._raw(KEY_CURRENT_FAN_GEAR)
        return str(gear) if isinstance(gear, int) and gear > 0 else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._write_program(PROGRAM_OFF)
        else:
            await self._write_program(self._last_active_program)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        program = PRESET_TO_PROGRAM.get(preset_mode)
        if program is None:
            raise HomeAssistantError(f"Unknown mode: {preset_mode}")
        await self._write_program(program)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        preset = self._require_active_preset()
        await self._write_setpoints(preset.temperature_keys, temperature)

    async def async_set_humidity(self, humidity: int) -> None:
        preset = self._require_active_preset()
        await self._write_setpoints(preset.humidity_keys, humidity)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if fan_mode not in _FAN_MODES:
            raise HomeAssistantError(f"Unknown fan mode: {fan_mode}")
        preset = self._require_active_preset()
        register = REGISTERS_BY_KEY[preset.fan_gear_key]
        gear = int(fan_mode)
        await self._write_optimistic(
            {register.key: gear} | {KEY_CURRENT_FAN_GEAR: gear},
            [(register, gear)],
        )

    def _handle_coordinator_update(self) -> None:
        self._prune_optimistic()
        program = self._program()
        if program is not None and program != PROGRAM_OFF:
            self._last_active_program = program
        self.async_write_ha_state()

    async def _write_program(self, program: int) -> None:
        register = REGISTERS_BY_KEY[KEY_PROGRAM_MODE]
        await self._write_optimistic({KEY_PROGRAM_MODE: program}, [(register, program)])

    async def _write_setpoints(self, keys: tuple[str, ...], value: float) -> None:
        writes: list[tuple[HombeeAirRegister, int]] = []
        optimistic: dict[str, int] = {}
        for key in keys:
            register = REGISTERS_BY_KEY[key]
            raw = register.encode_numeric(value)
            writes.append((register, raw))
            optimistic[key] = raw
        readback_key = (
            KEY_CURRENT_TEMPERATURE_SETPOINT
            if keys[0].endswith("temperature_setpoint")
            else KEY_CURRENT_HUMIDITY_SETPOINT
        )
        optimistic[readback_key] = REGISTERS_BY_KEY[readback_key].encode_numeric(value)
        await self._write_optimistic(optimistic, writes)

    async def _write_optimistic(
        self,
        optimistic: dict[str, int],
        writes: list[tuple[HombeeAirRegister, int]],
    ) -> None:
        self._optimistic.update(optimistic)
        expires_at = monotonic() + OPTIMISTIC_HOLD_SECONDS
        for key in optimistic:
            self._optimistic_deadlines[key] = expires_at
        self.async_write_ha_state()
        try:
            for register, raw in writes:
                await self._runtime.async_write(register, raw)
        except HombeeAirModbusError as error:
            for key in optimistic:
                self._optimistic.pop(key, None)
                self._optimistic_deadlines.pop(key, None)
            self.async_write_ha_state()
            raise HomeAssistantError(str(error)) from error

    def _active_preset(self) -> PresetSetpoints | None:
        preset_mode = self.preset_mode
        if preset_mode is None or preset_mode == PRESET_OFF:
            return None
        return PRESET_SETPOINTS.get(preset_mode)

    def _require_active_preset(self) -> PresetSetpoints:
        preset = self._active_preset()
        if preset is None:
            raise HomeAssistantError("Select an active mode before changing setpoints")
        return preset

    def _raw(self, key: str) -> int | bool | None:
        self._prune_optimistic_key(key)
        if key in self._optimistic:
            return self._optimistic[key]
        return self._runtime.raw_value(key)

    def _numeric(self, key: str) -> float | None:
        raw = self._raw(key)
        register = REGISTERS_BY_KEY[key]
        if not isinstance(raw, int) or raw == register.unavailable_raw_value:
            return None
        value = register.decode_numeric(raw)
        if register.precision is not None:
            return round(value, register.precision)
        return value

    def _program(self) -> int | None:
        raw = self._raw(KEY_PROGRAM_MODE)
        return raw if isinstance(raw, int) else None

    def _flag(self, key: str) -> bool:
        return bool(self._raw(key))

    def _prune_optimistic(self) -> None:
        for key in tuple(self._optimistic):
            self._prune_optimistic_key(key)

    def _prune_optimistic_key(self, key: str) -> None:
        if key not in self._optimistic:
            self._optimistic_deadlines.pop(key, None)
            return
        if self._runtime.raw_value(key) == self._optimistic[key]:
            self._clear_optimistic_key(key)
            return
        deadline = self._optimistic_deadlines.get(key)
        if deadline is not None and monotonic() >= deadline:
            self._clear_optimistic_key(key)

    def _clear_optimistic_key(self, key: str) -> None:
        self._optimistic.pop(key, None)
        self._optimistic_deadlines.pop(key, None)
