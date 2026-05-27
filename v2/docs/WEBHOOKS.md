# ITIPS Webhooks — Consumer Integration Guide

> Push channel for AI/sensor/incident events. Register a URL once and
> the Jetson will POST signed JSON to it whenever something happens —
> no polling, no long-lived HTTP connections, no client SDK.

---

## 1. When to use webhooks vs. SSE vs. polling

| Channel | Endpoint | When it fits |
|---|---|---|
| **Webhooks** (this doc) | Your HTTPS URL | You're a service with a stable IP/DNS. You want events delivered to *your* HTTP server. Best for alarm panels, gate controllers, ops bots, downstream platforms. |
| **Server-Sent Events** | `GET /api/alerts/stream` | You hold a long-lived HTTP connection (browser dashboards, terminal viewers). Same payloads, but stream-pushed instead of POSTed. |
| **Polling** | `GET /api/alerts/latest` | Last resort. Misses transient state. Don't build new consumers on this. |

Webhooks are usually what you want for service-to-service integration.

---

## 2. Lifecycle

```
┌──────────────────┐  POST           ┌─────────────────┐
│ ITIPS Jetson     │ ───────────────▶│ Your endpoint   │
│ (WebhookDispatch)│  HMAC-signed    │  /webhooks/itips│
└──────────────────┘                 └─────────────────┘
        ▲                                      │
        │  CRUD on /api/webhooks/subscribers   │ 200 OK
        │                                      ▼
┌──────────────────┐                  ┌─────────────────┐
│ Operator         │                  │ Your service    │
│ dashboard / curl │                  │ acts on event   │
└──────────────────┘                  └─────────────────┘
```

1. Register your endpoint via `POST /api/webhooks/subscribers`. The
   response returns a secret **once**. Store it.
2. The Jetson POSTs JSON to your URL whenever a matching event fires.
3. Reply `2xx` within 10 seconds. Anything else is treated as a
   failure and retried with exponential backoff (up to 5 attempts).
4. After 20 consecutive failures the subscriber is auto-disabled.
   Re-enable with `PATCH /api/webhooks/subscribers/<id>` `{"enabled": true}`.

---

## 3. Registering a subscriber

### Create

```bash
curl -X POST http://<jetson-host>:5050/api/webhooks/subscribers \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://ops.example.com/webhooks/itips",
    "event_filter": ["incident.confirmed", "ai.validation"],
    "description": "ops slack bridge"
  }'
```

Response (the only time the secret is ever returned):

```json
{
  "ok": true,
  "subscriber": {
    "id": "9c3f...",
    "url": "https://ops.example.com/webhooks/itips",
    "event_filter": ["incident.confirmed", "ai.validation"],
    "secret": "whsec_xxxxxxxxxxxxxxxxxxxxxxxxx",
    "enabled": true,
    "created_at": 1716750000.0
  }
}
```

If you omit `secret`, ITIPS generates one for you (recommended). If you
omit `event_filter`, the subscriber gets every event kind (`["*"]`).

### Update / disable / re-enable

```bash
curl -X PATCH http://<jetson-host>:5050/api/webhooks/subscribers/<id> \
  -H 'Content-Type: application/json' \
  -d '{"enabled": false}'
```

### Rotate the secret

```bash
curl -X POST http://<jetson-host>:5050/api/webhooks/subscribers/<id>/rotate-secret
```

The response returns the new secret once. Update your consumer
immediately — signatures with the old secret will start failing.

### Delete

```bash
curl -X DELETE http://<jetson-host>:5050/api/webhooks/subscribers/<id>
```

### List supported event kinds

```bash
curl http://<jetson-host>:5050/api/webhooks/event-kinds
```

### Recent deliveries (audit)

```bash
curl 'http://<jetson-host>:5050/api/webhooks/deliveries?subscriber_id=<id>&limit=50'
```

### Test fire

```bash
curl -X POST http://<jetson-host>:5050/api/webhooks/subscribers/<id>/test
```

Sends a `test.ping` event to the subscriber, bypassing the event
filter. Useful for end-to-end smoke tests during integration.

---

## 4. Event kinds

| Kind | Fires when |
|---|---|
| `incident.preliminary` | First event opens an incident (camera or sensor). |
| `incident.confirmed`   | Promoted via dwell, face_intruder, fire or smoke. |
| `incident.finalized`   | Idle sweep closed the incident and produced an evidence package. |
| `incident.verdict`     | ThreatEvaluator closed its decision window. One per window with `verdict` ∈ `authorized` / `intruder` / `uncertain`. **This is what alarm panels and the hub should subscribe to** — `incident.confirmed` only fires on the INTRUDER path, while this kind covers all three outcomes. |
| `alert.behaviour`      | Camera-side rule (line cross, region intrusion, loitering, motion, sensor alarm). |
| `alert.face_intruder`  | Face detection that did not match the enrolled personnel set. |
| `alert.personnel_seen` | Face matched against the workers group — presence log. |
| `alert.plate_capture`  | Plate read from camera `TrafficCar` / `TrafficVehicle` events. |
| `alert.fire`           | Camera-side fire classification. |
| `alert.smoke`          | Camera-side smoke classification. |
| `sensor.event`         | Raw wireless sensor activation (door, contact, PIR, vibration). Fires once per accepted event, **before** the per-zone cooldown gate. |
| `ai.validation`        | OpenAI vision validator returned a verdict for an alert. |
| `heartbeat`            | Periodic health beat. High-volume — opt in only when you need it. |
| `test.ping`            | Manual ping from the dashboard `/test` endpoint. |

Always-current list:  `GET /api/webhooks/event-kinds`.

Subscribers can mix and match by listing the kinds in `event_filter`,
or pass `["*"]` to receive everything.

---

## 5. Payload schema

Every webhook POST carries the same envelope:

```json
{
  "id":          "f6e4...",            // UUID per logical event, stable across retries
  "kind":        "incident.confirmed",
  "timestamp":   1716750123.45,        // Unix seconds, float
  "site_id":     "LAG-001",
  "operator_id": "ACME",
  "device_id":   "JETSON-LAG-001-01",
  "incident_id": "inc-2026-05-26-001", // null when the event is not bound to an incident
  "data":        { … kind-specific body … }
}
```

The `data` field carries the event-specific body. The shapes are
stable, but new optional fields may be added without notice — your
consumer must ignore unknown keys.

### `incident.preliminary` / `incident.confirmed` / `incident.finalized`

```json
{
  "stage":  "confirmed",
  "signal": "fire",                       // present on `confirmed`
  "confirmation_signals": ["fire","dwell:loitering"],  // present on `finalized`
  "camera_id": 1,
  "timestamp_utc": "2026-05-26T19:45:59Z"
}
```

### `incident.verdict`

Final decision-window outcome from the ThreatEvaluator. One per closed
window per camera, regardless of which triggers opened it. Switch on
`verdict` to route the alarm panel; `alarm_fired` tells you whether a
siren actually fired (false when the hub was disarmed).

```json
{
  "verdict":     "intruder",              // "authorized" | "intruder" | "uncertain"
  "camera_id":   1,
  "armed":       true,                    // AX PRO arming state at decision time
  "alarm_fired": true,                    // intruder + armed only
  "triggers":    ["camera:line_cross", "sensor:zone-3"],
  "trigger_details": [
    {"kind":"camera:line_cross","direction":"LeftToRight","rule_name":"Perimeter"},
    {"kind":"sensor:zone-3","zone_id":3,"zone_name":"Side Door","sensor_type":"PIR"}
  ],
  "samples":     12,                      // frames evaluated in the window
  "saw_face":    true,                    // false ⇒ uncertain (back-to-camera)
  "best_no_match_sim": 0.21,              // intruder only
  "person_uid":  "p-7",                   // authorized only
  "person_name": "Alex",                  // authorized only
  "similarity":  0.91,                    // authorized only
  "window_s":    14.8
}
```

### `alert.behaviour`

```json
{
  "alert_type": "line_crossing",          // or region_intrusion / loitering / sensor_alarm / motion …
  "camera_id":  1,
  "details": {
    "zone_id":    7,
    "zone_name":  "perimeter",
    "track_id":   42,
    "class_name": "person"
  }
}
```

### `alert.face_intruder`

```json
{
  "camera_id": 1,
  "bbox":      [123.0, 88.0, 245.0, 310.0],
  "name":      "INTRUDER"
}
```

### `alert.plate_capture`

```json
{
  "camera_id":     1,
  "plate_number":  "ABC123DE",
  "plate_color":   "white",
  "vehicle_color": "black",
  "speed":         null,
  "confidence":    0.87
}
```

### `sensor.event`

```json
{
  "zone_id":     12,
  "event_type":  "doorContact",
  "event_state": "alarm",
  "zone_name":   "shelter door",
  "source":      "axpro",
  "received_ts": 1716750123.45,
  "raw":         { …vendor payload… }
}
```

### `ai.validation`

```json
{
  "scenario":         "behavior_intrusion",
  "verdict":          "real",
  "category":         "human",
  "confidence":       0.92,
  "summary":          "A person is present inside the highlighted zone.",
  "model":            "gpt-4o-mini",
  "tokens_used":      14477,
  "cached":           false,
  "should_suppress":  false,
  "should_escalate":  false,
  "context":          { …same context the caller passed to validate()… }
}
```

---

## 6. HTTP headers

| Header | Purpose |
|---|---|
| `Content-Type: application/json` | Body is always UTF-8 JSON. |
| `User-Agent: itips-webhooks/1` | Lets you allowlist us. |
| `X-ITIPS-Event` | Same as `kind` in the body — handy for routing without parsing. |
| `X-ITIPS-Event-ID` | Same as `id` in the body. Idempotency key. |
| `X-ITIPS-Delivery` | UUID **per delivery attempt**. Changes across retries of the same event. |
| `X-ITIPS-Timestamp` | Unix seconds (integer) when the signature was computed. |
| `X-ITIPS-Signature` | `sha256=<hex>` HMAC of `<timestamp>.<body>` using your secret. |

---

## 7. Verifying the signature

The signed string is `<timestamp>.<raw body bytes>`, and the signature
is HMAC-SHA256 with your subscriber secret. Use a constant-time
comparison and reject signatures whose timestamp drifts more than 5
minutes from now — this prevents replay of captured payloads.

### Python

```python
import hmac
from hashlib import sha256
import time

def verify(secret: str, headers, body: bytes) -> bool:
    ts = int(headers["X-ITIPS-Timestamp"])
    sig = headers.get("X-ITIPS-Signature", "")
    if not sig.startswith("sha256="):
        return False
    if abs(time.time() - ts) > 300:           # reject >5 min drift
        return False
    msg = f"{ts}.".encode() + body
    expected = "sha256=" + hmac.new(secret.encode(), msg, sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
```

### Node.js

```js
import crypto from "node:crypto";

export function verify(secret, headers, rawBody) {
  const ts = parseInt(headers["x-itips-timestamp"], 10);
  const sig = headers["x-itips-signature"] || "";
  if (!sig.startsWith("sha256=")) return false;
  if (Math.abs(Date.now() / 1000 - ts) > 300) return false;
  const msg = Buffer.concat([Buffer.from(`${ts}.`), rawBody]);
  const expected =
    "sha256=" + crypto.createHmac("sha256", secret).update(msg).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(sig));
}
```

> Always verify against the **raw** request body, not a re-serialised
> object. Re-serialising changes whitespace and key order, and the
> signature won't match.

### Go

```go
import (
    "crypto/hmac"
    "crypto/sha256"
    "encoding/hex"
    "fmt"
    "strconv"
    "time"
)

func Verify(secret string, headers http.Header, body []byte) bool {
    ts, err := strconv.ParseInt(headers.Get("X-ITIPS-Timestamp"), 10, 64)
    if err != nil { return false }
    if abs64(time.Now().Unix()-ts) > 300 { return false }

    sig := headers.Get("X-ITIPS-Signature")
    if !strings.HasPrefix(sig, "sha256=") { return false }

    mac := hmac.New(sha256.New, []byte(secret))
    fmt.Fprintf(mac, "%d.", ts)
    mac.Write(body)
    expected := "sha256=" + hex.EncodeToString(mac.Sum(nil))
    return hmac.Equal([]byte(expected), []byte(sig))
}
```

### curl (manual smoke test)

```bash
# Replace WHSEC and TS with the real values from the request:
echo -n "${TS}.${BODY}" | openssl dgst -sha256 -hmac "${WHSEC}"
# Compare against X-ITIPS-Signature (without the "sha256=" prefix).
```

---

## 8. Idempotency and deduplication

`X-ITIPS-Event-ID` is **stable across retries** of the same logical
event. Persist it for at least 24 hours; if you see the same ID twice,
treat the second one as a no-op.

`X-ITIPS-Delivery` is **per attempt** and changes for every retry. It
is useful for log correlation but not for deduplication.

---

## 9. Retries and failure behaviour

- Per-attempt timeout: 10 seconds (configurable: `ITIPS_WEBHOOKS_TIMEOUT_S`).
- Retry attempts: 5 with exponential backoff `2s, 4s, 8s, 16s, 32s`.
- Failures recorded in `GET /api/webhooks/deliveries` for audit.
- 20 consecutive delivery failures → subscriber auto-disabled.
- Across a Jetson restart, in-flight retries are dropped (the
  dispatcher's queue is in-memory). Persistent durability lives in the
  Sync Agent for cloud-side flows; webhook delivery is best-effort.
  If you cannot tolerate the loss, also consume the SSE feed.

A `2xx` reply within the timeout is taken as success. Anything else
(`4xx`, `5xx`, connection refused, TLS error, slow body) is a retry.

---

## 10. Operational notes

- **Network reach**: subscribers must be reachable from the Jetson at
  the moment of delivery. Cloud endpoints typically work; LAN endpoints
  are usually fine because the dispatcher runs on the same site network.
- **HTTPS recommended**: HTTP is allowed for LAN consumers but the
  signature does not encrypt the payload — anything sensitive should
  ride a TLS-terminated URL.
- **No PII in URLs**: don't put tokens or identifiers in the path —
  use a stable URL and rely on the HMAC for authenticity.
- **Per-tenant isolation**: each subscriber has its own secret;
  rotating one does not affect the others.

---

## 11. Quick troubleshooting

| Symptom | Likely cause |
|---|---|
| Subscriber stops firing | Check `consecutive_failures` in `GET /api/webhooks/subscribers/<id>` — at 20 it auto-disables. |
| Signatures fail | Re-check you're hashing the **raw** body bytes, not a re-serialised JSON. |
| `403 unknown event kind` on create | The kind moved or was typo'd. Hit `/api/webhooks/event-kinds` for the live list. |
| Events arrive twice | Different `X-ITIPS-Delivery`, same `X-ITIPS-Event-ID` → it's a retry; dedupe on event ID. |
| Test ping never arrives | Subscriber may be disabled (`enabled=false`) or URL unreachable from the Jetson — check `GET /api/webhooks/deliveries`. |
