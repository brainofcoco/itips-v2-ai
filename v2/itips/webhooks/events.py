"""Event kinds and the WebhookEvent envelope sent to subscribers."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# Public registry of event kinds a subscriber may filter on. The
# dashboard exposes this list at GET /api/webhooks/event-kinds so
# operators can see what's available without consulting the code.
#
# When you add a kind here, also document it in docs/WEBHOOKS.md.
EVENT_KINDS: dict[str, str] = {
    # Incident lifecycle — published by AlertEngine
    "incident.preliminary": "First event opens an incident (camera or sensor).",
    "incident.confirmed":   "Incident promoted via dwell, face_intruder, fire or smoke.",
    "incident.finalized":   "Idle sweep finalised the incident and produced an evidence package.",
    "incident.verdict":     "ThreatEvaluator's final decision-window verdict: authorized, intruder, or uncertain. One event per closed window — what alarm panels and the hub should listen for.",

    # Per-alert records — also published by AlertEngine
    "alert.behaviour":      "Camera-side behaviour rule (line cross, region intrusion, loitering, motion).",
    "alert.face_intruder":  "Face detection that did not match the enrolled personnel set.",
    "alert.personnel_seen": "Face matched against the workers group — presence log, not an incident.",
    "alert.plate_capture":  "Plate read from camera TrafficCar / TrafficVehicle events.",
    "alert.fire":           "Camera-side fire classification.",
    "alert.smoke":           "Camera-side smoke classification.",
    "heartbeat":            "Periodic health beat — typically high-volume, opt-in only.",

    # Sensor events — published by SensorDispatcher when an AX PRO
    # zone fires (independent of any incident promotion).
    "sensor.event":         "Wireless sensor activation (door, contact, PIR, vibration).",

    # AI second-opinion — published by OpenAIValidator
    "ai.validation":        "OpenAI vision validator returned a verdict for an alert.",

    # Test fixture — fired from POST /api/webhooks/subscribers/<id>/test
    "test.ping":            "Manual ping from the dashboard for connectivity testing.",
}


@dataclass
class WebhookEvent:
    """The JSON body posted to subscriber URLs.

    `id` is stable across retries of the same delivery so consumers can
    deduplicate. `delivery_id` (in the HTTP header) changes per attempt.
    """

    kind: str
    data: dict[str, Any]
    site_id: str | None = None
    operator_id: str | None = None
    device_id: str | None = None
    incident_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "site_id": self.site_id,
            "operator_id": self.operator_id,
            "device_id": self.device_id,
            "incident_id": self.incident_id,
            "data": self.data,
        }

    def to_json(self) -> bytes:
        return json.dumps(self.to_payload(), separators=(",", ":"),
                          sort_keys=True, default=str).encode("utf-8")


def alert_record_to_event(record: dict[str, Any]) -> WebhookEvent | None:
    """Translate an AlertEngine `_record(...)` dict into a WebhookEvent.

    Returns None when the record's `kind` doesn't map to a published
    event kind (defensive — keeps unknown kinds from being broadcast).
    """
    raw_kind = str(record.get("kind") or "")
    mapped = _ALERT_KIND_MAP.get(raw_kind)
    if mapped is None:
        return None
    return WebhookEvent(
        kind=mapped,
        data={k: v for k, v in record.items()
              if k not in {"site_id", "operator_id", "device_id",
                           "incident_id", "timestamp_utc", "monotonic_ns"}},
        site_id=record.get("site_id"),
        operator_id=record.get("operator_id"),
        device_id=record.get("device_id"),
        incident_id=record.get("incident_id"),
    )


# AlertEngine uses lowercase event kinds without the `alert.` prefix
# (see itips/alerts/engine.py — "behaviour", "face_intruder", etc.).
# We translate those into the public webhook kinds here.
_ALERT_KIND_MAP: dict[str, str] = {
    "behaviour":       "alert.behaviour",
    "face_intruder":   "alert.face_intruder",
    "personnel_seen":  "alert.personnel_seen",
    "plate_capture":   "alert.plate_capture",
    "fire":            "alert.fire",
    "smoke":           "alert.smoke",
    "heartbeat":       "heartbeat",
}
