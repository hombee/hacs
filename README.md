# Hombee for Home Assistant

[![Continuous integration](https://github.com/hombee/hacs/actions/workflows/continuous-integration.yaml/badge.svg)](https://github.com/hombee/hacs/actions/workflows/continuous-integration.yaml)
[![Continuous delivery](https://github.com/hombee/hacs/actions/workflows/continuous-delivery.yaml/badge.svg)](https://github.com/hombee/hacs/actions/workflows/continuous-delivery.yaml)

Home Assistant Community Store integrations maintained by Hombee.

## Features

- **Hombee Voice** adds cloud transcription, AI conversation and
  AI-generated speech to native Home Assistant Assist pipelines. Requires
  Home Assistant 2026.9 or later and a Hombee Pro association.
- **Managed lighting** controls brightness for dimmable lamps and color
  temperature for lamps that support it. Both attributes are applied in the
  first physical `light.turn_on` call. Room profiles set daily targets and
  optional illuminance sensors adjust brightness to the measured light.
  Manual brightness and color changes remain independent and last until the
  lamp turns off. Active lights are checked once per minute.
- **Hombee Air** controls ventilation programs, temperature, humidity, and fan
  gears over Modbus TCP. It also exposes controller readings, configuration,
  alarms, and automatic clock synchronization.
- **Doorbells** connect entrance ring sources, cameras, and optional entrance
  actions to Hombee, with persisted delivery of ring events to the Hombee gateway.
- **Shelly configuration** exposes local Gen2+ discovery, inspection, and generic
  RPC to Hombee MCP. Agents read official Shelly documentation directly; new RPC
  methods and JSON fields do not require an integration update.
- **Hombee invitations** let HA administrators email a link from Hombee's
  **Home configuration → Users**. The first signed-in Hombee account to use the
  link receives credentials for the invited HA user and is linked to their
  Person. Administrators can select an existing user or create a new one;
  existing permissions and Person tracking settings are preserved. Links expire
  after seven days and invitation access can be revoked without deleting the
  HA user or Person. This requires the matching Hombee app/backend release and
  the administrator's connected instance to have a reachable remote URL.

Jump to [installation](#installation), [Hombee Air](#hombee-air),
[Hombee Voice](#hombee-voice), [managed lighting](#managed-lighting),
[doorbells](#doorbells), or [integration APIs](#integration-apis).

## Requirements

Home Assistant 2026.9 or later and HACS 2.0 or later are required. Configuration
uses Home Assistant's integration UI; there is no `hombee:` YAML setup.

Air control runs locally and requires a reachable Hombee Air Modbus TCP
controller. Managed lighting runs locally using lights already provided by
other HA integrations. Neither feature requires a Voice association.
Voice requires internet access and a Hombee Pro association. Doorbell delivery
requires a pairing with the Hombee gateway and internet access.

## Migration from `hombee_air`

The integration domain is `hombee`. This is a breaking domain change.
Before upgrading an installation with existing `hombee_air` managed lights,
use the old integration's `hombee_air/managed_lights/list` command to read its
revision, then send `hombee_air/managed_lights/reconcile` with that revision
and `lights: []`. This restores the original physical entity IDs. Removing
the old entry alone does not restore those IDs.
Then remove the old integration entries, update through HACS, restart HA,
and add Hombee again, including Air units. New installations and subsequent
light discovery do not use a reconciliation API.

## Installation

1. Install HACS in Home Assistant.
2. Open HACS, choose **Custom repositories**, and add this repository as an
   **Integration** repository.
3. Install **Hombee** from HACS.
4. Restart Home Assistant.
5. Open **Settings > Devices & services > Add integration** and search for
   **Hombee**.
6. Choose managed lighting, a Hombee Air unit, or Hombee Voice.

## Hombee Air

### Connect a unit

Choose **Hombee Air** when adding the integration, then enter the controller's
host or IP address, display name, installation ID, and TCP port (default `502`).
The integration checks that the controller responds before adding it. Each unit
has its own entry and device; use a distinct installation ID for each unit.
The Modbus device address is `1`.

### Climate controls

The main `climate` entity provides these controls and readings:

| Feature | Behavior |
| --- | --- |
| Programs | `off`, `economy`, `comfort`, `comfort_plus`, and `manual`, selected through presets |
| Power | Standard `climate.turn_on` and `climate.turn_off`; turning on restores the last active program remembered during the current runtime, with comfort as the initial default |
| Temperature | Current room temperature and target temperature; target range 5–35 °C in 0.1 °C steps |
| Humidity | Current room humidity and target humidity; target range 0–100% in steps of 1 percentage point |
| Fan gear | `1`, `2`, or `3` through `climate.set_fan_mode`; changes the active program's fan gear, or the manual supply fan gear in manual mode |
| Operating state | `auto` while an active program is selected and `off` when stopped |
| Current action | Heating, cooling, drying, fan operation, idle, or off, based on controller demand signals |

Select a program using `climate.set_preset_mode`. The climate entity exposes
`auto` as its selectable HVAC mode; heating and cooling are resolved by the
controller. Program selection is separate from the controller's additional
low-level program and season settings.

Temperature and humidity changes require an active preset. In economy, comfort,
and comfort+, a change updates both that program's heating and cooling setpoints.
In manual mode it updates the corresponding manual setpoint. To keep different
seasonal values, edit their individual `number` entities instead.

For example, select comfort before setting its target temperature:

```yaml
actions:
  - action: climate.set_preset_mode
    target:
      entity_id: climate.hombee_air
    data:
      preset_mode: comfort
  - action: climate.set_temperature
    target:
      entity_id: climate.hombee_air
    data:
      temperature: 22.5
```

Entity IDs in the examples are illustrative. Use your unit's actual entity ID.

### Humidity on the dashboard

The Air climate entity exposes measured and target humidity and supports
`climate.set_humidity`. Target humidity ranges from 0 to 100% in steps of
1 percentage point. Select an active preset before changing the target:
economy, comfort, and comfort+ update both seasonal setpoints; manual updates
the manual setpoint. Humidity changes are rejected while the unit is off.

Home Assistant 2026.9 adds climate support to the tile card's
[Target humidity feature](https://www.home-assistant.io/dashboards/features/#target-humidity).
Add **Target humidity** in the tile card's feature editor, or use this example
with your unit's entity ID:

```yaml
type: tile
entity: climate.hombee_air
features:
  - type: target-humidity
```

The same target is available to automations:

```yaml
action: climate.set_humidity
target:
  entity_id: climate.hombee_air
data:
  humidity: 55
```

### Readings and controller configuration

In addition to the main climate entity, the integration exposes the controller
catalog as native Home Assistant entities. Numeric readouts become `sensor`
entities, read-only flags become `binary_sensor` entities, numeric settings become
`number` entities, enumerated settings become `select` entities, and writable
flags become `switch` entities. Display names and enumerated options have English
and Polish translations.

| Controller area | Available information and settings |
| --- | --- |
| Overview | Room, return, supply, exhaust, and outdoor temperatures; room, return, and supply humidity; active temperature, humidity, and CO₂ setpoints; current program and gear; fan, filter, mixing damper, and humidifier status or requests |
| Program setpoints | Separate heating/cooling temperature and humidity targets for economy, comfort, and comfort+; CO₂ targets; manual targets and supply gear; standby thresholds and direction |
| Season and BMS control | Program and BMS enable, season source and dates, transition-period settings, heating/cooling locks, lead sensors, and automatic season/humidity settings |
| Fan configuration | Supply/return speed, pressure, and airflow settings for gears 1–3; program gears; regulation flags; minimum and maximum fan requests |
| Alarms | Sensor, fire, antifreeze, overheating, fan, pressure, filter, motor, inverter, communication, heat recovery, heat pump, humidifier, UV, and operating-time alarms, plus alarm reset |
| Diagnostics and service settings | CO₂, pressure, airflow and absolute humidity readings; internal demands and I/O; work counters; PID tuning and deadbands; duct limits, delays, protection and preheating settings; mixing presets; auxiliary regulators; manual overrides; controller clock and schedule registers |

The catalog includes controller vacation, special-day, and timed-event schedule
registers. They are exposed individually; there is no dedicated schedule editor.
Available readings and their practical effect depend on the unit's installed
equipment and controller configuration.

Service and advanced writable entities are categorized as configuration.
Advanced writable entities are disabled by default and can be enabled from
Home Assistant's entity settings. Read-only alarm and advanced diagnostic
entities are categorized as diagnostics. For the complete list of keys,
addresses, scales, options, and access tiers, see the
[register catalog](custom_components/hombee/registers.py).

### Alarms, clock, and communication

Active coded alarms appear both as problem binary sensors and as warnings in
Home Assistant **Repairs**, with the unit name and controller alarm code.
Warnings clear when the alarm clears and are removed when the entry unloads.
The reset-alarm switch writes the controller's reset command.

The controller clock is checked at setup and after configuration refreshes.
Hombee writes Home Assistant's local date and time if the controller's date is
invalid or differs by more than 15 minutes.

Live registers are polled every 10 seconds; setpoints and configuration every
60 seconds, with a refresh requested after writes. The climate entity becomes
unavailable if either polling group fails. Register entities follow the
availability of their own group.

Writes are queued at no more than one per second per unit. Pending writes to
the same point are combined so the latest queued value wins. Entity controls
show the requested value immediately for up to 30 seconds while awaiting
readback; failed writes clear that temporary value and report an error.

### Direct register actions

For advanced automations, `hombee.write_register` accepts `installation_id`,
`key`, and an integer `value`; `hombee.write_coil` accepts `installation_id`,
`key`, and a boolean `state`. Both require a known writable catalog point of
the matching Modbus type. Read-only points are rejected.

Register actions take **raw values**, without the scaling applied by `number`
and `climate` entities. For example, a register with scale `0.1` uses raw `225`
for a displayed value of `22.5`. These actions do not apply the entity's display
limits or ask for confirmation on service/advanced points. Prefer the ordinary
entity actions for daily control.

## Hombee Voice

### Association and pipeline

Open **Pro configuration** in the Hombee app after subscribing. Associate a
connected Home Assistant instance using an administrator connection and a
reachable remote URL. Pro includes one instance; additional Home Assistant
packages add instance slots. Hombee creates a **Hombee Voice** Assist
pipeline automatically. Select it on your Assist device or voice satellite.

The integration provides native `stt`, `conversation`, and `tts` entities.
The pipeline is created with local intents preferred; cloud conversation uses
Home Assistant's Assist tools and exposed entities. Home Assistant executes
device actions and owns conversation history. Cloud conversation is limited to
five requests per turn; an exchange that needs more returns an error.

Speech recognition, conversation, and speech synthesis use models selected by
the Hombee backend. Model selection can change independently of HACS updates.
English and Polish speech are supported. Each audio request is limited to
30 seconds. Requests send audio, conversation context, and the available
exposed-entity tools to Hombee's metered OpenAI gateway. Your OpenAI API key
is never stored in Home Assistant. Spoken replies are AI-generated.

Existing Assist devices and satellites supply the audio. The transcription
provider accepts 16 kHz, 16-bit mono PCM in WAV format; generated speech is
returned as WAV. Selecting Hombee Voice in the integration menu alone does not
grant cloud access: complete the association in Hombee Pro configuration.

### Usage and subscription

Each instance has a US$2 API allowance per UTC calendar month. The gateway
reserves enough for a bounded voice turn before recognition or cloud-controlled
device actions: one recording, up to five conversation requests, and one spoken
reply. This protects the spoken reply even if concurrent turns use the remaining
allowance. Pro configuration separates consumed and reserved amounts, warns at
80% usage, and shows the reset date in your local time zone. Unused capacity is
released when speech finishes or after five minutes for interrupted turns.
Requests with uncertain provider outcomes retain their individual reservations.
Cloud stages stop when there is insufficient capacity for a complete turn.
Usage resets on the first day of the next month, and removing or reconnecting
an instance does not reset it. A separate Assist pipeline using local providers
can still run local intents when the cloud allowance is exhausted.

Cancelling renewal preserves paid access until expiry. Expired Pro access
disables cloud voice; expired additional packages pause assignments beyond the
remaining allowance, in association order. Remove an association in Pro
configuration to select another instance. The app explains missing or outdated
HACS installations and offers refresh after installation or upgrade.

### Repair and diagnostics

Use **Repair connection** in Pro configuration to renew the scoped token and
restore the three Hombee pipeline providers after a restore or reinstall.
It preserves the association, existing pipeline ID, other pipeline preferences and usage.
A connection pointing at a different HA identity must be explicitly reassociated.
Unavailable HA connections must first be restored in Settings > Connections.

Failures create a Home Assistant notification without a model call. Conversation
failures use bundled English/Polish spoken notices, also without an API call.
An action may already have completed if a later stage fails; check its state
before repeating a command. Hombee's latest request diagnostics contain only
stage, duration, time, error code and support reference. They do not store audio,
transcripts, prompts, tool arguments or credentials. Home Assistant's own
conversation history and optional pipeline debug recordings remain under its
normal settings.

## Managed lighting

### Discovery and existing automations

After enabling managed lighting, Hombee automatically discovers registered
dimmable lights at startup and when new lights appear. Light groups
are excluded to avoid wrapping both a group and its members. No reconciliation
API call is needed.

Brightness-only and RGB lights can receive brightness adaptation. Color
temperature adaptation requires the light to advertise `color_temp` support.
On/off-only lights and disabled entities are excluded.

Existing public `light.*` IDs are preserved for dashboards, scenes, and
automations. Hombee forwards commands to hidden `*_physical` entities belonging
to the original integrations. Removing the managed lighting entry restores the
original IDs and hidden status. Use the public entities when controlling lamps.

Hombee adjusts lights that are already on; presence automations decide when to
turn them on or off. An ordinary public `light.turn_on` without explicit
brightness or color receives the current automatic settings in the first
physical command. A physical power switch may still expose the lamp firmware's
startup state before Home Assistant receives its report.

### Circadian color temperature

Use `switch.hombee_circadian_lighting` to control circadian lighting throughout
the home through the UI or the standard `switch.turn_on` and `switch.turn_off`
service API. The setting survives restarts. Disabling it preserves ordinary
light control and stops automatic temperature writes. Enabling it clears manual
color overrides and updates currently active lights. The `sun` integration must be
enabled for the daytime curve; without solar data the warm temperature is used.

The default curve runs from 2200 K at night toward 5000 K at solar noon, then
back toward warm light at sunset. Commands are clamped to each lamp's supported
temperature range. Color and brightness adaptation are independent.

### Brightness profiles and activities

Brightness has its own `switch.hombee_adaptive_brightness`. Open the managed
lighting entry's **Configure** menu to change the default daily profile,
assign a room profile and lux sensor, or set an individual lamp's automatic
brightness limits. Room activity selectors support reading, cooking, relaxation,
night lighting, and the daily schedule. All settings survive restarts.

The initial brightness schedule uses 07:00 wake time, 23:00 sleep time, a
30-minute morning ramp, and a two-hour evening ramp. It starts with 100% day,
40% relaxation, and 5% night brightness. Adjust these values to your rooms and
sleep schedule. Existing managed-light installations gain automatic brightness
when upgrading; turn off the brightness switch to retain manual brightness.

Profiles use Home Assistant's local time zone, including daylight saving time
and sleep times after midnight. Rooms inherit the default profile until given
their own settings. Individual lamp settings control brightness/color adaptation
separately, minimum and maximum automatic brightness, and transitions from
0 to 60 seconds. Explicit manual commands take priority over automatic limits.

| Activity | Behavior |
| --- | --- |
| `auto` | Follow the daily brightness schedule |
| `reading`, `cooking` | Hold the task target, including at night |
| `relax` | Hold the relaxation target |
| `night` | Hold the night target |
| `inherit` | Room selectors only: follow the home activity |

Activities persist until changed, survive restarts, and do not turn lights on.
Restoring a room's default profile also removes its room activity override.

### Room illuminance feedback

A room profile can use an illuminance sensor reporting in `lx`. Default targets
are 300 lx for day, 100 lx for relaxation, 10 lx for night, and 500 lx for reading
or cooking. A sensor belongs to a room profile, not the home default.

The room controller averages the brightness of active lamps still under
automatic brightness control and applies a shared percentage target within each
lamp's limits. It checks once per minute and corrects at most once per fresh
sensor report, by no more than five percentage points. Readings are smoothed;
correction stops within 10% of the target or 2 lx, whichever is larger.

Readings taken before a command settles are ignored for at least ten seconds
or the configured transition, whichever is longer. Missing, invalid, wrong-unit,
or stale readings fall back to the scheduled/activity brightness; the default
maximum sensor age is 300 seconds. The controller holds configured limits when
a target is unreachable and never turns a lamp off to meet a lux target.

### Manual control and resuming adaptation

Explicit brightness and color commands pause only their respective adaptation.
Scenes follow the same rules. Effects pause both attributes; flash commands do
not change stored overrides. Changes reported by an already-on physical lamp
can also pause the changed attribute, with a settling window to avoid treating
Hombee's own transitions as manual changes.

Overrides survive reloads and restarts while a lamp remains on. Turning the
lamp off clears them. Turning a global adaptation switch on clears overrides
for that attribute; changing a profile or activity preserves manual overrides.

Use `hombee.resume_adaptation` to resume either attribute without changing the
power state. Both fields default to `true`; specify `false` to preserve an
override for the other attribute:

```yaml
action: hombee.resume_adaptation
target:
  entity_id: light.kitchen
data:
  brightness: true
  color: false
```

Public lights expose `brightness_control` (`disabled`, `manual`, `schedule`, or
`lux`), `brightness_manual_override`, `color_manual_override`, `lighting_activity`,
`target_brightness`, `target_illuminance`, and `illuminance_sensor` for diagnostics.
Use one adaptation controller per attribute and lamp to avoid competing writes.

See [the lighting setup guide](docs/lighting.md) for sensor placement, room
configuration, automation examples, manual-control edge cases, and troubleshooting.

## Doorbells

### Sources, cameras, and entrance actions

Doorbell support is initialized with the Hombee integration and managed through
Hombee's authenticated API. It does not add a separate entry to the integration
setup menu. Pairing connects this Home Assistant instance to the Hombee gateway.
Administrators can save up to 50 named doorbells and enable or disable each one.

| Ring source | Trigger |
| --- | --- |
| Event entity | A new event timestamp whose `event_type` matches the configured type |
| Binary sensor | An exact configured transition, such as `off` to `on` |
| Automation | An explicit call to `hombee.report_doorbell_ring` for a doorbell configured with a manual source |

Standard event entities with device class `doorbell` are suggested automatically,
along with cameras belonging to the same HA device. Suggestions must be saved
before they become configured doorbells. Startup states and transitions involving
`unknown` or `unavailable` are ignored. Event-source timestamps more than
60 seconds from the current time are ignored as well.

Each doorbell can associate up to eight camera entities and one optional entrance
action: unlock or unlatch a lock, open a cover, press a button, or run a script.
The integration publishes these associations for the Hombee client; receiving a
ring does not execute the entrance action. Registry identities preserve the
associations when entities are renamed. Removing a registered source disables
its doorbell; removed cameras and action entities are omitted.

Doorbell visibility requires read access to its source entity. Camera visibility
and control access to the entrance action are checked separately. Configuration
and pairing require an HA administrator.

### Report a ring from an automation

Copy the saved doorbell ID from its Hombee setup. For a manual-source doorbell:

```yaml
action: hombee.report_doorbell_ring
data:
  doorbellId: "<saved-doorbell-id>"
  eventId: "<stable-source-event-id>"
```

The optional `eventId` should identify one ring. Reuse it when retrying the same
event and use a new value for a new ring. Omitting it generates a new ID for each
call. Disabled doorbells and doorbells using other source kinds reject this action.

### Delivery and diagnostics

Rings are sent to the paired Hombee gateway. The first associated camera is used
for an optional snapshot, requested at 640 pixels wide with a two-second timeout.
JPEG and PNG images up to 256,000 bytes are accepted. Camera failure or an
unsupported image does not suppress the ring. This integration sends snapshots;
it does not implement a video or two-way audio call.

The persisted outbox holds up to 20 pending events and survives HA restarts.
Each event expires after 60 seconds and permits at most six delivery attempts.
Connection errors, timeouts, HTTP 408/429, and server errors are retried with
backoff. Other unsuccessful HTTP responses stop delivery of that event.
Duplicate pending event IDs are suppressed; the same source ID produces the
same ring event ID. The status API reports the latest delivery diagnostic.

## Integration APIs

These commands use Home Assistant's authenticated `/api/websocket` connection.
They support the Hombee app and other authenticated clients; feature settings
remain stored in Home Assistant.

| Command | Purpose and access |
| --- | --- |
| `hombee/lighting/get` | Administrator: read configuration, revision, areas, lights, sensor candidates, and current lighting decisions |
| `hombee/lighting/update` | Administrator: update profiles, individual lamps, activities, global switches, or resume adaptation using `expected_revision` |
| `hombee/assist/status` | Administrator: inspect protocol, instance identity, integration version, association, pipeline readiness, and latest failure diagnostic |
| `hombee/assist/configure` | Administrator: apply the Hombee-issued association and create or repair the native Assist pipeline on the matching instance |
| `hombee/doorbells/status` | Read configured doorbells filtered by HA permissions; administrators also receive discovery suggestions and configuration candidates |
| `hombee/doorbells/configure` | Administrator: pair the matching instance with the Hombee gateway |
| `hombee/doorbells/save` | Administrator: replace the doorbell list using `instanceId`, `expectedRevision`, and a unique `requestId` |
| `hombee/shelly/list` | Administrator: list configured devices and optionally discover unconfigured Gen2+ devices using mDNS |
| `hombee/shelly/inspect` | Administrator: inspect one device by `device_id` or identify an explicit local `host` |
| `hombee/shelly/call` | Administrator: call one advertised RPC `method` with arbitrary JSON `params` on a stable `device_id` |

Lighting supports the `profile`, `reset_profile`, `light`, `activity`, `enabled`,
and `resume` operations. It rejects stale revisions before changing settings.
Doorbell saves also reject stale revisions; repeating the last successful
`requestId` is treated as an already completed request. Read the current state
before editing and check it again if a connection fails during a write.

Lighting configuration is also available through the Hombee app's homeowner
MCP tools for reading configuration, editing/resetting profiles, editing lamp
settings, selecting activities, toggling adaptation, and resuming it. See the
[lighting API and MCP reference](docs/lighting.md#configure-through-hombee-app-mcp)
for tool names, payloads, pagination, errors, and revision handling. The local
lighting controller continues running when the app or MCP client disconnects.

### Shelly configuration through MCP

The homeowner MCP tools are `shelly_list_devices`, `shelly_inspect_device`, and
`shelly_call`. Requests run from HA to the device's local HTTP RPC endpoint;
Shelly cloud access is not required. Devices must be reachable from HA.
This bridge supports Gen2 and later; Gen1 HTTP APIs are not supported.

List with `discover` (default `true`), `limit` (1–200, default 50), and `offset`.
The response includes `total`, `discovery_errors`, and official documentation
links. Discovery browses Shelly mDNS advertisements without scanning the subnet.
Inspect a device using exactly one of `device_id` or `host`; `port` defaults to
80 and port 443 uses HTTPS. A manual host must resolve only to local LAN IPs.
The bridge returns a MAC-based `device_id`, firmware, advertised methods,
configuration, status, and the first component page when available. Use generic
`Shelly.GetComponents` calls with `offset` to retrieve subsequent pages.

Configured devices reuse the current native Shelly integration's credentials.
Add protected devices through that integration; passwords never leave HA.
Unconfigured devices discovered or identified by host remain known until HA
restarts, after which listing or host inspection identifies them again.
Every operation checks the device's identity before sending RPC. Calls reject
methods absent from the device's current `Shelly.ListMethods` response.

For example, after inspection, a local WebSocket call can set an auto-off timer:

```json
{
  "id": 1,
  "type": "hombee/shelly/call",
  "device_id": "shelly-aabbccddeeff",
  "method": "Switch.SetConfig",
  "params": {"id": 0, "config": {"auto_off": true, "auto_off_delay": 60}}
}
```

Agents must consult the [official Gen2+ API](https://shelly-api-docs.shelly.cloud/gen2/)
for parameters and semantics, accounting for model, firmware, and device profile.
The live method list confirms availability but supplies no parameter schemas.
Read the relevant configuration before a change and verify it afterwards.
The bridge serializes RPC calls per device, limits request JSON to 64 KiB and
response JSON to 256 KiB, and redacts credential fields in responses.
Successful WebSocket delivery does not imply successful RPC: check `rpc_error`,
which is `null` on success, and `result` for the vendor result.
A timeout or broken response after RPC dispatch returns `shelly_call_unconfirmed`;
inspect the device before further action and never retry automatically.

## Updates

Install updates through HACS when a new GitHub Release is available.

## Development

Install dependencies and run the checks:

```bash
uv sync --locked --extra dev
bun install --frozen-lockfile
uv run --locked ruff check .
uv run --locked black --check .
uv run --locked pytest
```

To update dependencies:

```bash
uv lock --upgrade
bun update
```
