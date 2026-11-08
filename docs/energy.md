# Local energy

Hombee can publish local readings from Home Assistant's **official SolarEdge
Modbus** integration (`solaredge_modbus`, introduced in HA 2026.10). Configure
the inverter in that integration first. HA owns its connection and ten-second
reading schedule. Hombee reads registered HA entities; it never opens a second
Modbus connection, reads registers, forces entity updates, or changes inverter
settings. `solaredge_modbus_multi`, cloud SolarEdge entities, and entities whose
names merely resemble SolarEdge sensors are not sources.

The installation API starts with Hombee itself. It needs neither a Voice entry
nor Hombee Pro and continues working when the phone is closed. The Hombee app
associates an authorized cloud energy site with a selected inverter group,
one optional grid meter, and selected batteries. Each Hombee account has a
separate opaque publisher ID, scoped token, binding list, revision, and outbox.
Removing one site or account does not remove another account's bindings.

## Measurements and missing capabilities

Discovery uses official config-entry IDs and device-registry IDs, never display
names. Official entity unique IDs contain inverter and attachment serials (or
the official slot fallback when an attachment has no serial). Entity renames
preserve association; removing or replacing hardware requires reviewing the
mapping. Disabled entities are excluded. Select the meter at the site's grid
connection, rather than a production or sub-load meter.

Power is normalized to watts and state of charge to percent. Native meter
export-positive power is negated to Hombee's import-positive convention. Native
battery discharge-positive DC power is negated to Hombee's charge-positive
convention. For a coherent selected inverter group, home load is the sum of
inverter AC power plus the selected grid meter's normalized import power. This
supports negative inverter AC power during grid charging. Negative derived load
is treated as invalid rather than clamped to zero. Check meter placement and
direction during commissioning with a known import/export condition.

Inverter AC or DC power is **not pure PV production**. The current adapter does
not publish `solarPower` without independent evidence of the PV topology. Solar production remains available through cloud history; the coherent live
local frame leaves PV unknown. It does not estimate PV from
battery power or energy counters. Native inverter AC is used only for the
supported coherent load calculation, which assumes the selected official
inverters contain the site's DC-coupled battery conversion; external AC-coupled
battery systems need a separately proven measurement topology.

Battery capabilities are detected independently. HA's model 713
`storage_state_of_charge` can provide SoC without battery power. A real reported
0% remains zero; missing sensors remain unknown. Multiple battery powers are
summed only when all selected batteries provide them. Multiple battery SoCs are
omitted because capacity weights are unavailable. Grid connection comes only
from official `on_grid` binary sensors: all selected inverters must agree on
`connected` or `disconnected`; every other combination is `unknown`.

Freshness uses HA `last_reported`, which advances when an unchanged value is
reported, rather than `last_changed`. Restored, unknown, unavailable, malformed,
non-finite, wrong-unit, stale, future-dated, and incoherent samples cannot trigger
a local action. The default maximum age is 30 seconds and maximum skew five
seconds. A mixed group is not relabelled as a new coherent measurement.

## Delivery

One heartbeat every ten seconds coalesces official state reports and publishes
one normalized observation per binding group. The `sourceId` is the first
selected config-entry ID; the observation contains the coherent group totals,
so a shared grid meter is not counted once per inverter. The backend owns the
account/site authorization and receipt timestamp.

The persisted outbox retains at most one latest value per binding, expires
observations after 30 seconds, and preserves their original observation times
over restart. Sequence numbers persist across boots; each boot has a new session
ID. Transient transport errors, 408, 429 and 5xx use bounded backoff, respecting
numeric and HTTP-date `Retry-After`. New values replace older values without
resetting backoff. Authorization failures (401/403) stop that publisher's
delivery and local actions until an explicit configure/repair. An authenticated
409 `binding_obsolete` response pauses only its binding; `revision_obsolete`
discards only the obsolete sample. Responses to an old token, generation, or
configuration revision cannot revoke or renew a successor configuration. Redirects are
not followed and credentials are sent only to the fixed HTTPS Hombee origin.

## Explicit local load policies

Telemetry is the default: policies start disabled and have no rules. An HA
administrator can opt into up to 16 rules per binding. A rule has bounded
numeric conditions, hysteresis, a continuous dwell interval (0–3600 seconds),
an optional manual override deadline, and exactly one registered HA `switch`
action (`turnOn` or `turnOff`). No arbitrary services, inverter controls,
registers, flashing, or firmware operations are exposed.

One switch can belong to only one rule across all account publishers. A rule
acts once per condition entry, verifies the resulting switch state, and pauses
durably if readback is uncertain. External switch state changes create a durable
manual override. An explicit configuration save resumes policy evaluation;
restarts preserve manual overrides and reset dwell timers. Removing or replacing
a target stops its rule. Rules never replay queued observations.

Local actions continue during a transient internet outage while their inputs
remain fresh. Decision authorization lasts at most **24 hours per binding** after configure
or that binding's last accepted telemetry delivery. Another binding's successful
delivery cannot renew its authorization. After that, rules pause with
`policy_authorization_expired` until authenticated delivery resumes. A rejected
token pauses immediately. This bounds remote revocation delay when HA is
offline. Already applied load states are not reversed on expiration or removal;
operators retain normal HA control. Treat these as convenience load controls,
not protective or emergency power controls.

## Administrator WebSocket protocol

Commands use HA's authenticated `/api/websocket` and require an administrator.
All mutations include `protocol: 1`, the exact `instanceId`, a server-owned
`publisherId`, a unique `operationId`, and `expectedRevision`. The version comes
from Hombee's installed manifest. The HA instance ID comes from HA's
`helpers.instance_id` and is independent of connection URLs.

| Command | Result or mutation |
| --- | --- |
| `hombee/energy/status` | Without `publisherId`: discovery only, revision 0, configured false, empty bindings. With it: only that publisher's current bindings/revision and health diagnostics. |
| `hombee/energy/configure` | Atomically replace `bindings` and install `publisher: {url, token, generation, bindingRevision}`. `url` must be `https://hombee.app`, token length 64–128, binding revision equals the resulting scoped revision. |
| `hombee/energy/save` | Atomically replace that publisher's complete binding list and policies; preserve grant generation and increment binding revision. |
| `hombee/energy/unconfigure` | Remove one `bindingId`; preserve other groups. Final removal deletes its token and clears pending observations. |

Binding shape is `{bindingId, site: {provider, id}, sourceIds, gridMeterId?,
batteryIds, policy}`. Policy shape is `{enabled, maxObservationAgeSeconds,
maxSkewSeconds, rules}`. A condition is `{field, minimum?, maximum?, hysteresis}`;
at least one bound is required. A rule is `{id, enabled, conditions,
dwellSeconds, action: {entityId, service}, manualOverrideUntilMs?}`.

Repeating an operation with the exact same body returns the current status
without applying it twice. Reusing its ID with a changed body is rejected.
Idempotency hashes are persisted for the latest 128 operations; revision checks
still reject obsolete requests. Read status after uncertain transport outcomes
and preserve the original request for retries. Secrets, hashes and request
bodies are never returned in status or diagnostics.

Delivery uses `POST /api/energy/local/instances/{instanceId}/observations` with a
separate energy bearer. The protocol contains `publisherId`, `generation`,
`sessionId`, persistent `sequence`, `bindingRevision`, `bindingId`, `sourceId`,
`observedAtMs`, quality, grid state and normalized telemetry. It never accepts
backend commands to operate HA. The checked-in `energy_configure.json` and
`energy_observation.json` test fixtures mirror the app's shared protocol fixtures.

## Release coordination

Release the matching backend first, then this HACS integration, then the app.
Older installations keep cloud energy behavior until local pairing is explicitly
configured. This repository change does not deploy or publish any release.
