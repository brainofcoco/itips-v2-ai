"""Per-camera capability probe — cheap HTTP checks with 2–3s timeouts.

Result feeds the dashboard's Health tab and the CapabilityRouter,
which decides whether each camera needs the Jetson fallback path.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

import requests

from itips.camera.dahua_http import DahuaCameraEndpoint
from itips.utils.clock import now_iso

logger = logging.getLogger(__name__)


# ─── result shape ─────────────────────────────────────────────────────


# Status conventions:
#   "ok"      — capability is available and we verified it just now
#   "missing" — camera returned 4xx / empty body; feature simply isn't there
#   "error"   — network or auth failure (camera unreachable, timeout, etc.)
#   "unknown" — passive check: we haven't observed it yet (could still work)
#   "n/a"     — capability doesn't apply to this camera class
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"
STATUS_UNKNOWN = "unknown"
STATUS_NA = "n/a"


@dataclass
class CheckResult:
    name: str
    label: str        # human-friendly UI label
    status: str
    category: str     # used for grouping in the dashboard
    detail: Optional[str] = None
    backup_hint: Optional[str] = None  # what to do if this is missing/error


@dataclass
class CameraHealth:
    camera_id: int
    endpoint: str
    model: Optional[str] = None
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def green_count(self) -> int:
        return sum(1 for c in self.checks if c.status == STATUS_OK)

    @property
    def red_count(self) -> int:
        return sum(1 for c in self.checks if c.status in (STATUS_MISSING, STATUS_ERROR))


# ─── individual probes ────────────────────────────────────────────────


def _probe(name: str, label: str, category: str, backup_hint: Optional[str] = None):
    """Decorator: wrap any probe fn so an exception lands as STATUS_ERROR.

    Keeps each probe body short — just return (status, detail).
    """
    def decorator(fn: Callable[[DahuaCameraEndpoint], tuple[str, Optional[str]]]):
        def wrapped(endpoint: DahuaCameraEndpoint) -> CheckResult:
            try:
                status, detail = fn(endpoint)
            except requests.RequestException as exc:
                status, detail = STATUS_ERROR, exc.__class__.__name__
            except Exception as exc:  # noqa: BLE001
                status, detail = STATUS_ERROR, str(exc)
            return CheckResult(
                name=name, label=label, status=status, category=category,
                detail=detail, backup_hint=backup_hint if status in (STATUS_MISSING, STATUS_ERROR) else None,
            )
        return wrapped
    return decorator


@_probe("reachable", "Reachable on LAN", "core",
        backup_hint="Check IP / VLAN / camera power. Without reach, nothing else works.")
def probe_reachable(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/magicBox.cgi", params={"action": "getDeviceType"}, timeout=2.0)
    if r.status_code == 200 and r.text.strip():
        return STATUS_OK, r.text.strip().splitlines()[0]
    return STATUS_MISSING, f"HTTP {r.status_code}"


@_probe("snapshot", "Snapshot (/cgi-bin/snapshot.cgi)", "media",
        backup_hint="Without snapshots, evidence packages have no still images. Wire a Jetson-side RTSP grab.")
def probe_snapshot(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/snapshot.cgi", params={"channel": 1}, timeout=3.0)
    if r.status_code == 200 and (r.headers.get("Content-Type", "").startswith("image/")
                                  or (r.content[:2] == b"\xff\xd8")):
        return STATUS_OK, f"{len(r.content)} bytes"
    return STATUS_MISSING, f"HTTP {r.status_code}"


@_probe("mjpeg_sub", "MJPEG substream", "media",
        backup_hint="Switch the dashboard to RTSP→Jetson decode for this camera.")
def probe_mjpeg_sub(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/mjpg/video.cgi", params={"channel": 1, "subtype": 1},
               timeout=2.0, stream=True)
    try:
        if r.status_code == 200 and "multipart" in r.headers.get("Content-Type", "").lower():
            return STATUS_OK, r.headers.get("Content-Type")
        return STATUS_MISSING, f"HTTP {r.status_code}"
    finally:
        r.close()


@_probe("mjpeg_main", "MJPEG main stream", "media",
        backup_hint="Main stream is likely H.264-only. Use substream MJPEG, or transcode on the Jetson.")
def probe_mjpeg_main(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/mjpg/video.cgi", params={"channel": 1, "subtype": 0},
               timeout=2.0, stream=True)
    try:
        if r.status_code == 200 and "multipart" in r.headers.get("Content-Type", "").lower():
            return STATUS_OK, r.headers.get("Content-Type")
        return STATUS_MISSING, f"HTTP {r.status_code}"
    finally:
        r.close()


_FACE_GROUP_RE = re.compile(r"GroupList\[")


@_probe("face_recognition_db", "Face recognition (onboard worker DB)", "ai",
        backup_hint="Fall back to Jetson InsightFace SCRFD+ArcFace for this camera.")
def probe_face_db(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/faceRecognitionServer.cgi",
               params={"action": "findGroup"}, timeout=3.0)
    if r.status_code != 200:
        return STATUS_MISSING, f"HTTP {r.status_code}"
    # 200 with an empty body still counts as "endpoint exists" — empty
    # just means there are no groups yet.
    body = r.text or ""
    if _FACE_GROUP_RE.search(body) or body.strip() == "":
        return STATUS_OK, f"{body.count('GroupList[')} group(s)"
    return STATUS_OK, "endpoint reachable"


@_probe("anpr_redlist", "ANPR (TrafficRedList)", "ai",
        backup_hint="Deploy Plate Recognizer Stream on the Jetson for this camera's lane.")
def probe_anpr(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/recordFinder.cgi",
               params={"action": "find", "name": "TrafficRedList", "count": 1},
               timeout=3.0)
    if r.status_code == 200:
        return STATUS_OK, "list endpoint reachable"
    if r.status_code in (400, 404):
        return STATUS_MISSING, f"HTTP {r.status_code} (ANPR not enabled on this model)"
    return STATUS_MISSING, f"HTTP {r.status_code}"


@_probe("ptz", "PTZ control (ptz.cgi)", "control",
        backup_hint="Use camera as fixed view or wire ONVIF fallback.")
def probe_ptz(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/ptz.cgi", params={"action": "getPresets", "channel": 1},
               timeout=2.0)
    if r.status_code == 200:
        n = r.text.count("presets[")
        return STATUS_OK, f"{n} preset(s)"
    return STATUS_MISSING, f"HTTP {r.status_code}"


@_probe("deterrence", "TiOC deterrence (strobe + speaker)", "control",
        backup_hint="Wire an external horn speaker via AX PRO or this camera's alarm-out relay.")
def probe_deterrence(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/coaxialControlIO.cgi",
               params={"action": "getstatus", "channel": 1}, timeout=2.0)
    if r.status_code != 200:
        return STATUS_MISSING, f"HTTP {r.status_code}"
    body = r.text.lower()
    if "whitelight" in body or "speaker" in body:
        return STATUS_OK, r.text.strip().replace("\n", " ")
    return STATUS_MISSING, "no whitelight/speaker reported (non-TiOC camera)"


@_probe("ivs_rules", "IVS rule config readable", "ai",
        backup_hint="Define perimeter/intrusion zones on the Jetson instead.")
def probe_ivs_rules(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/configManager.cgi",
               params={"action": "getConfig", "name": "VideoAnalyseRule"},
               timeout=3.0)
    if r.status_code != 200:
        return STATUS_MISSING, f"HTTP {r.status_code}"
    n = r.text.count("VideoAnalyseRule[")
    if n == 0:
        return STATUS_OK, "endpoint reachable, no rules defined yet"
    return STATUS_OK, f"{n} rule(s) configured"


# Additional probes per docs/dahua-doc/. These disambiguate "endpoint
# reachable" from "feature actually deployed / will actually fire".

# Rule type field name differs by firmware generation — match both.
_IVS_RULE_TYPE_RE = re.compile(
    r"VideoAnalyseRule\[\d+\]\.(?:Object\.RuleType|Class)=(\w+)",
    re.IGNORECASE,
)


@_probe("ivs_rule_types", "IVS rule types deployed", "ai",
        backup_hint="No perimeter/intrusion rule on camera. Jetson must run zone logic for this lane.")
def probe_ivs_rule_types(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/configManager.cgi",
               params={"action": "getConfig", "name": "VideoAnalyseRule"},
               timeout=3.0)
    if r.status_code != 200:
        return STATUS_MISSING, f"HTTP {r.status_code}"
    types = sorted({t for t in _IVS_RULE_TYPE_RE.findall(r.text)})
    if not types:
        return STATUS_MISSING, "config readable but no rule type set"
    return STATUS_OK, ", ".join(types)


@_probe("face_group_channel", "Face group bound to channel 1", "ai",
        backup_hint="No face group on ch1 — onboard FR won't fire. Use Jetson InsightFace for this camera.")
def probe_face_group_channel(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/faceRecognitionServer.cgi",
               params={"action": "getGroup", "channel": 1}, timeout=3.0)
    if r.status_code != 200:
        return STATUS_MISSING, f"HTTP {r.status_code}"
    n = r.text.count("groupID")
    if n == 0:
        return STATUS_MISSING, "no group bound to channel 1"
    return STATUS_OK, f"{n} binding(s) on ch1"


@_probe("anpr_event_attach", "ANPR event subscription opens", "ai",
        backup_hint="Camera won't emit TrafficCarMeasurement. Plate Recognizer Stream must own this lane.")
def probe_anpr_event_attach(ep: DahuaCameraEndpoint):
    # Open + close — verifies the camera would deliver TrafficCarMeasurement.
    r = ep.get("/cgi-bin/eventManager.cgi",
               params={"action": "attach", "codes": "[TrafficCarMeasurement]"},
               timeout=2.0, stream=True)
    try:
        if r.status_code == 200 and "multipart" in r.headers.get("Content-Type", "").lower():
            return STATUS_OK, "attach accepted"
        return STATUS_MISSING, f"HTTP {r.status_code}"
    finally:
        r.close()


@_probe("white_light", "White light control (TiOC)", "control",
        backup_hint="Light not separately addressable. Strobe still fires through coaxial relay if probe_deterrence is OK.")
def probe_white_light(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/configManager.cgi",
               params={"action": "getConfig", "name": "Lighting"},
               timeout=2.0)
    if r.status_code != 200:
        return STATUS_MISSING, f"HTTP {r.status_code}"
    if "Lighting[" in r.text:
        return STATUS_OK, "Lighting config present"
    return STATUS_MISSING, "no Lighting array in response"


_ALARM_OUT_IDX_RE = re.compile(r"AlarmOut\[(\d+)\]")


@_probe("alarm_out", "Alarm-out relay available", "control",
        backup_hint="No alarm-out relay — deterrence fallback path unavailable. Wire AX PRO horn instead.")
def probe_alarm_out(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/configManager.cgi",
               params={"action": "getConfig", "name": "AlarmOut"},
               timeout=2.0)
    if r.status_code != 200:
        return STATUS_MISSING, f"HTTP {r.status_code}"
    # Distinct channel indices — same channel appears on Name=, Mode=, etc.
    n = len(set(_ALARM_OUT_IDX_RE.findall(r.text)))
    if n == 0:
        return STATUS_MISSING, "no AlarmOut array"
    return STATUS_OK, f"{n} channel(s)"


_SD_TOTAL_RE = re.compile(r"TotalBytes=(\d+)", re.IGNORECASE)


@_probe("sd_storage", "Onboard SD / eMMC storage", "core",
        backup_hint="No local storage — Tier 1 recording absent. If WAN drops mid-incident, only the Jetson NVMe has footage.")
def probe_sd_storage(ep: DahuaCameraEndpoint):
    r = ep.get("/cgi-bin/storageDevice.cgi",
               params={"action": "getDeviceAllInfo"}, timeout=3.0)
    if r.status_code != 200:
        return STATUS_MISSING, f"HTTP {r.status_code}"
    body = r.text or ""
    if "Detail" not in body and "list" not in body.lower():
        return STATUS_MISSING, "no storage info returned"
    m = _SD_TOTAL_RE.search(body)
    if m:
        gb = int(m.group(1)) // (1024 ** 3)
        return STATUS_OK, f"{gb} GB total"
    return STATUS_OK, "storage info returned"


_ACTIVE_PROBES = [
    # Order matters — the integration test's response sequence pins it.
    probe_reachable,
    probe_snapshot,
    probe_mjpeg_sub,
    probe_mjpeg_main,
    probe_face_db,
    probe_anpr,
    probe_ptz,
    probe_deterrence,
    probe_ivs_rules,
    probe_ivs_rule_types,
    probe_face_group_channel,
    probe_anpr_event_attach,
    probe_white_light,
    probe_alarm_out,
    probe_sd_storage,
]


# Passive checks pulled from EventTap state — "have we observed this code
# come out of this camera since we started up?"
_PASSIVE_EVENT_CODES: list[tuple[str, str, Optional[str]]] = [
    # (code, label, backup_hint)
    ("FaceDetection",        "FaceDetection event observed",     None),
    ("FaceRecognition",      "FaceRecognition event observed",
        "If this never fires, the onboard worker DB isn't matching — fall back to Jetson recognition."),
    ("CrossLineDetection",   "CrossLineDetection event observed",
        "Camera IVS line rule may not be configured."),
    ("CrossRegionDetection", "CrossRegionDetection event observed",
        "Camera IVS region rule may not be configured."),
    ("WanderDetection",      "WanderDetection event observed",   None),
    ("TrafficCarMeasurement", "TrafficCarMeasurement (ANPR) observed", None),
    ("FireDetection",        "FireDetection event observed",     None),
    ("SmokeDetection",       "SmokeDetection event observed",    None),
    ("VideoMotion",          "VideoMotion event observed",       None),
]


def _passive_checks(camera_id: int, codes_seen: dict[int, dict[str, int]]) -> list[CheckResult]:
    seen_for_cam = codes_seen.get(camera_id, {})
    out: list[CheckResult] = []
    for code, label, backup in _PASSIVE_EVENT_CODES:
        if code in seen_for_cam:
            out.append(CheckResult(
                name=f"event:{code}", label=label, status=STATUS_OK,
                category="events",
                detail=f"last seen at seq {seen_for_cam[code]}",
            ))
        else:
            out.append(CheckResult(
                name=f"event:{code}", label=label, status=STATUS_UNKNOWN,
                category="events",
                detail="not observed yet — walk in front of the camera or fire a rule",
                backup_hint=backup,
            ))
    return out


# ─── orchestration ────────────────────────────────────────────────────


def run_for_camera(
    endpoint: DahuaCameraEndpoint,
    camera_id: int,
    codes_seen: dict[int, dict[str, int]],
) -> CameraHealth:
    health = CameraHealth(camera_id=camera_id, endpoint=endpoint.safe_label())

    # Active probes — sequential so a single slow camera doesn't open 9 concurrent sockets.
    for probe in _ACTIVE_PROBES:
        result = probe(endpoint)
        health.checks.append(result)
        if result.name == "reachable" and result.status == STATUS_OK and result.detail:
            health.model = result.detail

    # Passive probes — instant, just dict lookups.
    health.checks.extend(_passive_checks(camera_id, codes_seen))
    return health


def run_for_all(manager, event_tap) -> dict[str, Any]:
    """Probe every camera the DahuaManager knows about.

    Parallel across cameras (one worker per camera) so total wall-clock
    is roughly the slowest single camera.
    """
    cameras = manager.all()
    codes_seen = event_tap.codes_seen_by_camera() if event_tap else {}
    if not cameras:
        return {"timestamp": now_iso(), "cameras": [], "summary": []}

    results: list[CameraHealth] = []
    with ThreadPoolExecutor(max_workers=min(4, len(cameras))) as pool:
        futures = {
            pool.submit(run_for_camera, c.endpoint, c.camera_id, codes_seen): c.camera_id
            for c in cameras
        }
        for fut in as_completed(futures, timeout=45):
            try:
                results.append(fut.result())
            except Exception:
                cam_id = futures[fut]
                logger.exception("health probe crashed for cam %d", cam_id)
                results.append(CameraHealth(camera_id=cam_id, endpoint="<unknown>"))
    results.sort(key=lambda h: h.camera_id)

    return {
        "timestamp": now_iso(),
        "cameras": [_serialise(h) for h in results],
        "summary": [_summary_for(h) for h in results],
    }


def _serialise(h: CameraHealth) -> dict[str, Any]:
    return {
        "camera_id": h.camera_id,
        "endpoint": h.endpoint,
        "model": h.model,
        "green_count": h.green_count,
        "red_count": h.red_count,
        "checks": [asdict(c) for c in h.checks],
        "backup_recommendations": [
            {"check": c.name, "label": c.label, "hint": c.backup_hint}
            for c in h.checks if c.backup_hint
        ],
    }


def _summary_for(h: CameraHealth) -> dict[str, Any]:
    return {
        "camera_id": h.camera_id,
        "model": h.model,
        "green_count": h.green_count,
        "red_count": h.red_count,
    }
