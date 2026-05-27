# AX PRO Hub — Integration and Operations Guide

> Everything an operator or engineer needs to know to get real sensor
> events flowing from a Hikvision AX PRO hub into ITIPS. Most of this
> is hard-earned during real installs — read it before debugging a
> "sensors aren't triggering" symptom.

---

## 1. The two channels

ITIPS subscribes to **two independent surfaces** on the hub. Each
catches things the other misses; do not remove either.

| Component | ISAPI path | What it's for |
|---|---|---|
| `AxProListener` (zone-status poll) | `/ISAPI/SecurityCP/status/zones` | Slow signals that stay steady for seconds — door `magnetOpenStatus`, sensor online/offline, low battery. Polled every 500 ms. |
| `AxProAlertStream` (long-poll) | `/ISAPI/Event/notification/alertStream` | Real-time hub-side push: `cidEvent`, `zoneEvent`, tamper, arm/disarm. Multipart-mixed; events arrive within ~200 ms of trigger. |

Both feed the same `SensorDispatcher` — duplicates are absorbed by the
existing per-zone cooldown.

**Why polling isn't enough:** A PIR's `alarm` flag flips for a fraction
of a second. The poller misses it. The alertStream catches it.

**Why alertStream isn't enough:** Some hub configs (Stay-armed, zone
bypassed, `armNoBypassEnabled=false`) silently drop the alarm event
before it reaches the stream. The poller still sees the underlying
`magnetOpenStatus` flip so we can dispatch on the raw edge.

---

## 2. The four hub-config preconditions

If sensors aren't firing, **the listener is almost never the bug**.
Check these in order — three out of four are silent on the API.

### 2.1 Subsystem arming mode must be **Away**, not Stay

`/ISAPI/SecurityCP/status/subsystemstatus`:

```json
{ "SubSys": { "id": 1, "arming": "stay", ... } }
```

`"stay"` (Perimeter Arming) suppresses alarms from interior detectors
— PIR, motion, PIR-cam, triple-signal — by design. ITIPS needs
`"away"` (Full Arming) on every subsystem that has sensors.

**Fix it via dashboard:**

```bash
curl -X POST http://<jetson>:5050/api/sensors/hub/arm-away \
  -H 'Content-Type: application/json' \
  -d '{"sub_ids": [1, 2, 3]}'
```

The route runs disarm → arm-away under the hood because the hub
rejects direct Stay → Away transitions with `subStatusCode:
"armedStatus"`.

### 2.2 Zone must not be **bypassed**

```json
{ "Zone": { "id": 1, "bypassed": true, ... } }
```

A bypassed zone never emits alarms in any arm mode, on either channel.
The Hik-Connect app's bypass toggle is unreliable — silently fails if
the zone is in any fault state.

**Fix it via dashboard:**

```bash
curl -X POST http://<jetson>:5050/api/sensors/zones/1/unbypass
```

If you get `502 armedStatus`, the parent subsystem is currently armed;
chain it through the disarm path instead:

```bash
curl -X POST http://<jetson>:5050/api/sensors/hub/arm-away \
  -H 'Content-Type: application/json' \
  -d '{"sub_ids":[1,2,3], "clear_bypass":[1]}'
```

That disarms, clears the bypass during the disarmed window, and re-arms
in one operator action.

### 2.3 `armNoBypassEnabled` — auto-rebypass on arm

`/ISAPI/SecurityCP/Configuration/zones/<id>`:

```json
{ "Zone": { "armNoBypassEnabled": false, ... } }
```

When this is `false`, the hub **silently re-bypasses any zone in a
fault state every time you arm**. That's why a manually un-bypassed
gate keeps reappearing as `bypassed: true` after every arm cycle.

| Value | Behaviour |
|---|---|
| `false` (default) | Auto-bypass faulty zones on arm. Arming always succeeds; faulty zones silently never fire. |
| `true`            | Hub refuses to arm any subsystem containing a faulty zone (`subStatusCode: "zoneFault"`). |

The right setting depends on whether you can tolerate silent
zone-drops. For ITIPS production sites, prefer `true` so a degraded
sensor is loud, not silent. Toggle:

```bash
curl -X PATCH http://<jetson>:5050/api/sensors/zones/1/config \
  -H 'Content-Type: application/json' \
  -d '{"key": "armNoBypassEnabled", "value": true}'
```

### 2.4 Magnetic contact alignment

Door contacts have two halves: a magnet on the moving panel and a reed
switch on the frame. When the door closes, the magnet must come within
**~10–15 mm** of the reed switch or the contact stays in `open` state.
Outdoor mounts drift with weather and use.

Verify alignment by closing the door and checking:

```bash
curl http://<jetson>:5050/api/sensors/hub/state | jq '.zones[] | select(.id==1)'
```

`magnetOpenStatus` must read `false` with the door physically shut. If
it reads `true`, the install is misaligned — fix the mount, no
software change will help.

---

## 3. Operator quickstart

For a brand-new install where sensors aren't firing, run this single
command — it walks all four preconditions for you:

```bash
JETSON=http://<jetson>:5050

# 1. Snapshot what the hub thinks right now
curl -s $JETSON/api/sensors/hub/state | jq '
  .subsystems[] | select(.enabled) | {id, name, arming, alarm}'

curl -s $JETSON/api/sensors/hub/state | jq '
  .zones[] | {id, name, bypassed, armed,
              magnetOpenStatus, alarm,
              sensorStatus, signal, charge}'

# 2. Disarm → un-bypass any stuck zones → arm Away
curl -s -X POST $JETSON/api/sensors/hub/arm-away \
  -H 'Content-Type: application/json' \
  -d '{"sub_ids":[1,2,3], "clear_bypass":[1]}' | jq

# 3. Watch the live stream while a sensor is triggered
curl -s $JETSON/api/sensors/events/recent | jq '.events[:10]'
```

If `.zones[].alarm` flips to `true` (or `events` grows) when a sensor
is triggered, the pipeline is working. If not, see **§4 Disambiguation**.

---

## 4. Disambiguation

When sensors don't fire after the four preconditions are correct,
here's the decision tree:

```
┌─────────────────────────────────┐
│ Does Hik-Connect mobile app     │
│ event log show the trigger?     │
└────────────┬────────────────────┘
             │
       ┌─────┴─────┐
       │           │
      YES          NO
       │           │
       ▼           ▼
 hub publishes  hub never sees
 events, ITIPS  the trigger —
 isn't seeing   pair / battery /
 them — verify  alignment / range
 alertStream    issue at the
 connected      sensor itself
```

To check whether the hub is even seeing the motion when you don't
trust Hik-Connect:

```bash
# Continuous diff of zone state for 60s — the only reliable way to
# catch raw sensor-level changes the hub records.
curl -s $JETSON/api/sensors/listener/zones | jq '.raw.ZoneList[] | .Zone | {id, name, alarm, magnetOpenStatus, sensorStatus, tamperEvident}'
```

Trigger your sensor, wait 2 s, re-run the command. **Some field must
change** if the hub is receiving the radio. If nothing changes after
multiple triggers, the sensor isn't reaching the hub (battery, signal,
pairing).

---

## 5. CID event codes

The alertStream carries `cidEvent` records with a numeric `code` field
following the SIA Contact-ID standard. The mapping built into
`itips/sensors/axpro_alertstream.py` covers:

| Code | Meaning | ITIPS event_type |
|---|---|---|
| 1100 | Medical | `medical` |
| 1110 | Fire | `fire` |
| 1111 | Smoke | `smoke` |
| 1120 | Panic | `panic` |
| 1130 | Burglary (generic) | `burglary` |
| 1131 | Perimeter zone tripped | `perimeter` |
| 1132 | Interior zone tripped (PIR) | `intrusion` |
| 1137 / 1144 | Tamper | `tamper` |
| 1150 | Vibration | `vibration` |
| 1170 | Door contact | `doorContact` |
| 1301 | AC power loss | `acLoss` |
| 1302 | Low battery | `lowBattery` |
| 1381 | RF sensor signal loss | `rfSensorLoss` |
| 1384 | RF sensor low battery | `rfSensorLowBattery` |
| 1401 | Open/close (disarm/arm) | `openClose` |
| 1407 | Remote arm/disarm | `remoteArmDisarm` |
| 1602 | Periodic test | `periodicTest` |
| 1627 / 1628 | Programming enter/exit | (logged, not dispatched) |

Unknown codes pass through as `cid_<N>` event_type. Add new mappings
in `_CID_CODE_MAP` in `axpro_alertstream.py`. Codes in the 1400–1499
range and 1627/1628 are operator actions, not alarms — they log at
INFO but don't open an incident.

---

## 6. Common hub error responses

| `subStatusCode` | When you'll see it | What to do |
|---|---|---|
| `armedStatus`   | `arm_away` / bypass-change while already armed | Disarm first. The `/hub/arm-away` route does this automatically. |
| `zoneFault`     | `arm_away` with `armNoBypassEnabled=true` on a faulty zone | Fix the zone (alignment, battery) or temporarily set `armNoBypassEnabled=false`. |
| `notSupport`    | Old ISAPI path on new firmware | Use the path documented here, not the one hikaxpro hardcodes. |
| `permissionDeny` | Account isn't admin | Use the installer account, not a "user" account. |

---

## 7. Health endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/sensors/listener/status` | Listener + alertStream connection state, `events_received` counter |
| `GET /api/sensors/listener/zones` | Raw `zone_status` payload + listener's cached edge state — for debugging field-name drift |
| `GET /api/sensors/hub/state` | Live subsystem + zone snapshot (arming, bypass, armNoBypassEnabled visible) |
| `GET /api/sensors/events/recent` | Recent dispatched events (last 200) |
| `POST /api/sensors/hub/disarm` | Disarm one or more subsystems |
| `POST /api/sensors/hub/arm-away` | Disarm + (optional) un-bypass + arm-away in one call |
| `POST /api/sensors/zones/<id>/unbypass` | Clear an operational bypass |
| `POST /api/sensors/zones/<id>/bypass` | Force a bypass |
| `PATCH /api/sensors/zones/<id>/config` | Patch a single zone-config field (`armNoBypassEnabled` is the common one) |

## 8. Siren (panic alarm)

Hikvision calls the manual-trigger siren **"One-Key Alarm"**. ITIPS
exposes it at `/api/sensors/hub/siren/*` so an operator can sound or
silence the hub from the dashboard, and the AlertEngine can
optionally sound it automatically on every confirmed incident.

| Endpoint | Purpose |
|---|---|
| `GET /api/sensors/hub/siren/status` | Per-siren state (online/offline, signal, battery, tamper) |
| `POST /api/sensors/hub/siren/start` | Sound the siren. Body: `{"sub_id": 1}` |
| `POST /api/sensors/hub/siren/stop` | Silence the siren |
| `POST /api/sensors/hub/siren/test` | Self-clearing burst (default 2 s, max 10 s). Stop fires from a try/finally so an interrupted test can't leave the siren ringing. |

**Under the hood:** the start path is
`PUT /ISAPI/SecurityCP/control/oneKeyAlarm/{sub_id}` with body
`{"OneKeyAlarm": {}}` and the stop is
`PUT /ISAPI/SecurityCP/control/clearAlarm/{sub_id}`. These paths are
the only ones the current firmware honours — the documented
`SirenCtrl` / `siren/{id}/start` paths return `notSupport` on this
hub.

### Auto-trigger on confirmed incidents

PRD §3.3 Stage 3 requires the Jetson to fire the hub siren the moment
a threat is confirmed. To enable:

```
ITIPS_AXPRO_AUTO_SIREN=true
ITIPS_AXPRO_AUTO_SIREN_SUBSYS=1
```

When set, every `incident.confirmed` lifecycle transition calls
`start_siren()` on the chosen subsystem. The siren keeps ringing until
an operator hits Silence or fires `POST /api/sensors/hub/siren/stop`.

Off by default — flip it on at production sites only, otherwise every
synthetic event during testing blasts the real siren.

### Hardware prerequisites

The siren is a wireless peripheral paired to the hub. `GET /api/sensors/hub/siren/status` must show at least one entry with `status: "online"` for the start command to make audible noise — otherwise the hub accepts the call (200 OK) but no siren is reachable. Common reasons for `status: "offline"`:

- battery dead (`charge: "fault"` or `chargeValue` near 0)
- mains-powered siren disconnected (`mainPowerSupply: false` for hours)
- out of radio range (`signal: 0`)
- not paired with the hub at all

Re-pair via the AX PRO web admin or Hik-Connect; software can't help.

---

## 8. Why the listener doesn't replace the hub UI

This is a thin operations layer, not a hub admin replacement. Things
**not** managed here:

- Pairing new sensors (requires the AX PRO web admin or installer app)
- Editing zone names, subsystem membership, zone type
- Firmware updates
- User accounts

If a setting isn't exposed via `/api/sensors/*`, do it in Hik-Connect
or the AX PRO web admin and snapshot the result via
`/api/sensors/hub/state` once it's applied.
