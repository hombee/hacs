# Lighting setup

Hombee keeps the existing public `light.*` entity IDs. Automations, dashboards,
and scenes should target those public lights. The hidden `*_physical` entities
belong to their original integrations and execute Hombee's commands.

Every registered dimmable lamp is discovered, including brightness-only and RGB
lamps. Temperature adaptation applies only to lamps reporting `color_temp`
support. On/off lamps, disabled entities, and Home Assistant light groups are
excluded. If another integration exposes a hardware group as a dimmable light,
disable adaptation for either that group or its members in Hombee's lamp settings
so that only one controller commands each physical lamp.

## Configure the home

1. Open **Settings > Devices & services > Hombee**, then **Configure** on the
   managed lighting entry.
2. Set the **Default daily profile** to your wake and sleep times. Hombee uses
   Home Assistant's local time zone, including daylight saving time. Sleep can
   fall after midnight. The morning and evening ramps must fit inside the awake
   period.
3. Assign public lamps to Home Assistant areas. Open **Room profile and sensor**
   to give a room its own targets. A room without its own profile inherits the
   default profile. Choose **Restore the default profile** to remove an exception
   and return its activity to the home default.
4. Open **Individual lamp settings** to set minimum and maximum automatic
   brightness, transition duration, and independent brightness and temperature
   adaptation. Check the minimum on the actual lamp. Some drivers turn off or
   flicker at very low commands. Limits apply to automatic commands; explicit
   brightness and scene settings take priority.
5. Add the activity selectors and the two global switches to your dashboard.
   Entity IDs depend on HA naming and language. Find the selectors on the Hombee
   device and use their actual IDs in automations.

The initial profile contains starting values to tune at home:

| Setting | Brightness | Sensor target |
| --- | ---: | ---: |
| Day | 100% | 300 lx |
| Relaxation | 40% | 100 lx |
| Night | 5% | 10 lx |
| Reading and cooking | 100% | 500 lx |

These sensor targets describe the desired reading at your sensor. They are not
a claim about the illumination of every surface in the room.

Without a sensor, the default daily schedule rises from night to day brightness
between 07:00 and 07:30. It stays at day brightness until 21:00, reaches the
relaxation value at 22:00, and reaches the night value at 23:00. Both parts of
the evening ramp use smooth curves. Brightness does not depend on sunset;
color temperature continues to follow the existing solar curve.

The `reading` and `cooking` activities hold the task targets even at night.
`relax` holds the relaxation targets, and `night` holds the night targets.
`auto` restores the daily schedule. The home selector supplies the activity
for rooms set to `inherit`, displayed as **Home default**. Once a room's selector
has been used, that room keeps its own selection until it is set back to `inherit`.
This preserves its room profile and sensor assignment.
Activity selections survive restarts and do not expire automatically.

## Add a room sensor

Use a room illuminance sensor that reports in `lx` and updates at least once
per minute. Place it where the measurement follows the light in the part of
the room you use. Keep it away from direct lamp glare and obstructions. The
controller expects combined daylight and artificial light. An outdoor sensor
or a sensor that cannot see the controlled lamps is unsuitable for this feedback
loop.

Select the sensor in that room's profile. A sensor cannot be assigned to the
home default because a single reading cannot describe every room. Brightness
percentages remain the fallback; the lux targets apply when a usable measurement
is available. Clear the sensor field to return the room to scheduled brightness.

Hombee uses one controller per room. It averages the reported brightness of
active lamps whose brightness remains automatic and applies a shared percentage
target, clamped to each lamp's limits. Manually controlled lamps remain untouched;
their light still contributes to the sensor reading. This equal-percentage
policy does not assume that different lamps produce equal light output.

The first turn-on uses the daily or activity brightness, or an existing valid
room correction. Hombee does not assume a calibrated relationship between lux
and lamp percentage. It then adjusts the lamps using feedback:

- Checks run once per minute, with at most one correction per fresh sensor report.
- A moving average keeps 70% of the previous filtered value and 30% of the new
  measurement. The first usable reading initializes the filter.
- Changes stop within 10% of the target, with a minimum tolerance of 2 lx.
- Each correction changes brightness by at most five percentage points.
- Readings taken before a command has settled are ignored. Settling takes at
  least ten seconds, or the configured transition duration if longer.
- Missing, negative, nonnumeric, nonfinite, wrong-unit, and stale readings fall
  back to the daily or activity brightness. The default maximum age is 300
  seconds, measured from the last sensor report, including unchanged reports.

Allow several minutes for convergence after large changes. If the target is
unreachable at a lamp's limits, Hombee holds the limit. The controller never
turns a lamp off to satisfy a lux target. Set suitable night limits and targets
after checking the actual light at home.

## Manual changes and scenes

An explicit brightness command pauses brightness adaptation for that lamp.
An explicit color command pauses color adaptation. Scenes use the same rules.
Effects pause both attributes, while flash commands do not change the stored
overrides. Hombee also avoids adaptation while a lamp reports an active effect.

An off state clears both overrides. The next public turn-on gets the current
automatic settings in its first physical command, subject to enabled policies.
Hombee retains overrides through a reload or restart while a lamp remains on.
If it is already off when the integration loads, overrides are cleared.

Use this action to resume only brightness without changing the power state:

```yaml
action: hombee.resume_adaptation
target:
  entity_id: light.kitchen
data:
  brightness: true
  color: false
```

The global switches independently enable brightness and color temperature.
Turning one on clears manual overrides for that attribute. Room activities and
profile edits keep manual overrides. Per-lamp adaptation settings still apply.

Changes reported directly by an already-on physical lamp also pause the changed
attribute. Hombee ignores its own HA contexts and contextless reports during
the expected transition plus two seconds. Devices that provide no command origin
cannot distinguish a simultaneous physical adjustment from a transition report
with certainty. Use the public light entities for scenes and automations so
Hombee can identify explicit commands reliably.

The first-command behavior applies when turning on through the public entity.
A wall switch that restores electrical power may let the lamp display its
firmware's startup state before HA receives a report. Configure the lamp's
power-on behavior separately if needed.

## Presence and automation

Hombee adjusts lamps that are already on. Presence automations decide when to
turn them on and off. For an ordinary automatic turn-on, omit brightness and
color so they are chosen by Hombee:

```yaml
action: light.turn_on
target:
  area_id: kitchen
```

Use a room's actual activity selector ID to request task lighting:

```yaml
action: select.select_option
target:
  entity_id: select.hombee_kitchen_lighting_activity
data:
  option: cooking
```

Return that selector to `auto` when the activity ends. Activities change targets
and leave the power state unchanged.

## Diagnose behavior

The public lamp exposes `brightness_control`, `brightness_manual_override`,
`color_manual_override`, `lighting_activity`, `target_brightness`,
`target_illuminance`, and `illuminance_sensor` attributes.

`brightness_control` reports `disabled`, `manual`, `schedule`, or `lux`.
`target_brightness` uses Home Assistant's 1–255 scale. A target can differ from
the actual brightness while an override, transition, or disabled policy applies.

If the lamp stays bright at night, check its activity, manual override, minimum
brightness, and the configured HA time zone. If lux control stays in `schedule`,
check the unit, last report time, sensor assignment, and whether a fresh report
has arrived after the last lamp transition. A sensor value remaining unchanged
is acceptable if the integration continues reporting it.

Use one brightness controller and one color controller per physical lamp.
Disable the corresponding adaptation in another integration before assigning
the same attribute to Hombee. Removing the Hombee managed lighting entry restores
the original physical entity IDs and their original hidden status.

## Configure through Hombee App MCP

The paired Hombee App change exposes seven homeowner MCP tools. They use the
selected installation's existing HA credentials and require an HA administrator.
The regulation loop runs locally in Home Assistant; the app and MCP client do
not need to remain connected.

| Tool | Purpose |
| --- | --- |
| `get_hombee_lighting_configuration` | Read settings, revision, areas, lamps, available illuminance sensors, and current decisions |
| `update_hombee_lighting_profile` | Patch home or room targets, schedule, and room sensor assignment |
| `reset_hombee_lighting_profile` | Remove a room profile and activity override to inherit home defaults |
| `update_hombee_light_settings` | Patch one lamp's independent policies, limits, and transition |
| `set_hombee_lighting_activity` | Set a home or room activity, including room-only `inherit` |
| `set_hombee_lighting_enabled` | Change either global adaptation switch, or both together |
| `resume_hombee_lighting_adaptation` | Resume selected attributes on explicitly selected public lights |

Read first, then pass the returned `revision` as `expectedRevision` on a write.
Use `profileKey: "default"` for the home, or the exact HA `area_id` for a room.
Profile and lamp edits change only supplied fields. Nested settings use the
same names as the returned configuration, such as `night_lux` and `adapt_color`.
Use an explicit `installationKey` when the account has multiple installations.

For example, setting `profileKey: "kitchen"` and
`settings: {"illuminance_sensor": "sensor.kitchen_lux", "task_lux": 450}` binds
that sensor and sets the reading/cooking target. Sensor selection is explicit;
the area's identifier alone does not select a sensor. A sensor in another area
can be selected deliberately. The configuration does not learn or persist a
lux-to-brightness calibration.

## Lighting WebSocket API v1

Both commands use HA's authenticated `/api/websocket` connection and an ordinary
increasing message `id`. `hombee/lighting/get` accepts `limit` from 1 to 500 and
`offset` from 0 to 100000, defaulting to 200 and 0. The complete `configuration`
is always returned. Diagnostic `areas`, `lights`, and `sensors` arrays are each
paginated independently, with counts in `totals`.

`hombee/lighting/update` requires `expected_revision`, `operation`, and `data`:

```json
{
  "id": 2,
  "type": "hombee/lighting/update",
  "expected_revision": "<revision returned by the latest read>",
  "operation": "profile",
  "data": {
    "profile_key": "kitchen",
    "settings": {"night_brightness": 8, "night_lux": 15}
  }
}
```

The supported operations are `profile`, `reset_profile`, `light`, `activity`,
`enabled`, and `resume`. Their payloads are validated in `lighting_api.py`.
The response contains `api_version: 1`, the current revision, configuration,
and diagnostics. `tests/fixtures/lighting_api_snapshot.json` is the response
contract fixture shared with the app tests.

UI and API configuration edits share a lock. A stale revision returns
`hombee_lighting_conflict` before changing any setting or lamp. Read again and
reconsider the edit. Revisions cover stored configuration and lamp membership,
not live sensor readings or manual overrides. Equivalent numeric settings keep
the same revision after reload.

Invalid input returns `hombee_lighting_invalid`; an unloaded integration returns
`hombee_lighting_not_loaded`. A storage failure returns
`hombee_lighting_storage_failed` and restores the previous in-memory settings.
Missing administrator access returns HA's authorization error. No operation
turns an inactive lamp on. Resume validates all targets before changing any.

If a connection fails during a write, its outcome is unknown. Read settings and
manual overrides before deciding whether another edit is needed. The app does
not automatically retry writes. Install the HACS change before enabling the app
tools; older integrations return an unsupported capability with update guidance.
