"""Home Assistant Repairs issues for active Hombee Air alarms."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import HombeeAirRuntime
from .registers import REGISTERS, HombeeAirRegister

ISSUE_TRANSLATION_KEY_ACTIVE_ALARM = "active_alarm"

_ALARM_REGISTERS = tuple(
    register
    for register in REGISTERS
    if register.section == "alarms" and register.alarm_code is not None
)


@callback
def async_start_alarm_issues(
    hass: HomeAssistant, runtime: HombeeAirRuntime, title: str
) -> None:
    """Creates and clears Repairs issues for active PLC alarm codes."""
    if runtime.alarm_issue_remove_listener is not None:
        return

    @callback
    def reconcile_alarm_issues() -> None:
        _async_reconcile_alarm_issues(hass, runtime, title)

    runtime.alarm_issue_remove_listener = runtime.fast.async_add_listener(
        reconcile_alarm_issues
    )
    reconcile_alarm_issues()


@callback
def async_stop_alarm_issues(hass: HomeAssistant, runtime: HombeeAirRuntime) -> None:
    """Stops alarm issue tracking and clears issues owned by this runtime."""
    if runtime.alarm_issue_remove_listener is not None:
        runtime.alarm_issue_remove_listener()
        runtime.alarm_issue_remove_listener = None

    for issue_id in runtime.active_alarm_issue_ids:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    runtime.active_alarm_issue_ids.clear()


def active_alarm_issue_id(slug: str, register: HombeeAirRegister) -> str:
    """Returns the stable Repairs issue id for an alarm register."""
    return f"alarm_active_{slug}_{register.key}"


@callback
def _async_reconcile_alarm_issues(
    hass: HomeAssistant, runtime: HombeeAirRuntime, title: str
) -> None:
    data = runtime.fast.data
    if data is None:
        return

    active_issues = {
        active_alarm_issue_id(runtime.slug, register): register
        for register in _ALARM_REGISTERS
        if bool(data.get(register.key))
    }

    for issue_id, register in active_issues.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_TRANSLATION_KEY_ACTIVE_ALARM,
            translation_placeholders={
                "unit": title,
                "alarm_code": register.alarm_code or "",
                "alarm_name": _alarm_name(register),
                "description": register.description,
            },
        )

    for issue_id in runtime.active_alarm_issue_ids - active_issues.keys():
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    runtime.active_alarm_issue_ids = set(active_issues)


def _alarm_name(register: HombeeAirRegister) -> str:
    if register.alarm_code is None:
        return register.name
    return register.name.removesuffix(f" ({register.alarm_code})")
