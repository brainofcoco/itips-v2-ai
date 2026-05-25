"""Onboard ANPR list client for Dahua cameras.

Replaces the Plate Recognizer Stream container on the Jetson. The camera
runs OCR onboard and cross-references plates against two lists it stores
itself:

* `TrafficRedList`  — authorized/fleet vehicles (gate opens, no alarm)
* `TrafficBlackList` — known offenders (escalate)

Reference: Dahua HTTP API V3.98, `recordUpdater.cgi` / `recordFinder.cgi`.

The Jetson never sees a frame for ANPR. It just maintains the two lists
and reacts to the `TrafficCarMeasurement` / `CarDrivingInOut` events the
camera emits when a plate is read.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from itips.camera.dahua_http import DahuaCameraEndpoint

logger = logging.getLogger(__name__)


RED_LIST = "TrafficRedList"
BLACK_LIST = "TrafficBlackList"


@dataclass(frozen=True)
class PlateRecord:
    rec_no: int
    plate_number: str
    list_type: str
    master_of_car: Optional[str] = None
    plate_color: Optional[str] = None
    vehicle_color: Optional[str] = None
    begin_time: Optional[str] = None
    cancel_time: Optional[str] = None


class DahuaPlateDB:
    """Per-camera ANPR allow/block list client."""

    def __init__(self, endpoint: DahuaCameraEndpoint, *, timeout: float = 8.0) -> None:
        self._endpoint = endpoint
        self._timeout = timeout

    # ─── CRUD ─────────────────────────────────────────────────────────

    def add(
        self,
        *,
        list_type: str,
        plate_number: str,
        master_of_car: Optional[str] = None,
        plate_color: Optional[str] = None,
        vehicle_color: Optional[str] = None,
        begin_time: Optional[str] = None,
        cancel_time: Optional[str] = None,
        open_gate: bool = False,
    ) -> int:
        """Insert a plate. Returns the camera-assigned recno."""
        _ensure_list_type(list_type)
        params = {"action": "insert", "name": list_type, "PlateNumber": plate_number}
        if master_of_car:
            params["MasterOfCar"] = master_of_car
        if plate_color:
            params["PlateColor"] = plate_color
        if vehicle_color:
            params["VehicleColor"] = vehicle_color
        if begin_time:
            params["BeginTime"] = begin_time
        if cancel_time:
            params["CancelTime"] = cancel_time
        if list_type == RED_LIST and open_gate:
            params["AuthorityList.OpenGate"] = "true"
        r = self._endpoint.post(
            "/cgi-bin/recordUpdater.cgi", params=params, timeout=self._timeout
        )
        r.raise_for_status()
        m = re.search(r"recno=(\d+)", r.text)
        if not m:
            raise DahuaPlateDBError(f"insert {list_type} missing recno: {r.text!r}")
        return int(m.group(1))

    def remove(self, *, list_type: str, rec_no: int) -> None:
        _ensure_list_type(list_type)
        r = self._endpoint.get(
            "/cgi-bin/recordUpdater.cgi",
            params={"action": "remove", "name": list_type, "recno": rec_no},
            timeout=self._timeout,
        )
        r.raise_for_status()

    def list(self, *, list_type: str, limit: int = 200) -> list[PlateRecord]:
        """Return rows in `list_type`. Raises `PlateListUnsupported` if the
        camera doesn't carry ANPR tables (e.g. a face-only WizMind model).
        """
        _ensure_list_type(list_type)
        params = {"action": "find", "name": list_type, "count": limit}
        r = self._endpoint.get(
            "/cgi-bin/recordFinder.cgi", params=params, timeout=self._timeout
        )
        if r.status_code in (400, 404):
            # Camera doesn't expose this table — common on face/IVS-only
            # WizMind models. Caller distinguishes this from a network error.
            raise PlateListUnsupported(
                f"{list_type} not available on this camera (HTTP {r.status_code})"
            )
        r.raise_for_status()
        return _parse_records(r.text, list_type)

    def find_plate(self, *, list_type: str, plate_number: str) -> list[PlateRecord]:
        _ensure_list_type(list_type)
        r = self._endpoint.get(
            "/cgi-bin/recordFinder.cgi",
            params={
                "action": "find",
                "name": list_type,
                "condition.PlateNumber": plate_number,
            },
            timeout=self._timeout,
        )
        r.raise_for_status()
        return _parse_records(r.text, list_type)


class DahuaPlateDBError(RuntimeError):
    """Protocol-level failure."""


class PlateListUnsupported(RuntimeError):
    """The camera doesn't carry the requested traffic table.

    Distinct from a network or auth failure — operators see a clean
    "ANPR not enabled" instead of a 502-style error.
    """


# ─── helpers ──────────────────────────────────────────────────────────


def _ensure_list_type(list_type: str) -> None:
    if list_type not in (RED_LIST, BLACK_LIST):
        raise ValueError(f"list_type must be {RED_LIST!r} or {BLACK_LIST!r}")


def _parse_records(body: str, list_type: str) -> list[PlateRecord]:
    """Parse `records[N].field=value` lines into PlateRecord."""
    rows: dict[int, dict] = {}
    pattern = re.compile(r"records\[(\d+)\]\.([A-Za-z]+)=(.*)")
    for line in body.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        idx, field, value = int(m.group(1)), m.group(2), m.group(3).strip()
        rows.setdefault(idx, {})[field] = value
    out: list[PlateRecord] = []
    for idx in sorted(rows.keys()):
        row = rows[idx]
        try:
            rec_no = int(row.get("RecNo", "0"))
        except ValueError:
            continue
        out.append(PlateRecord(
            rec_no=rec_no,
            plate_number=row.get("PlateNumber", ""),
            list_type=list_type,
            master_of_car=row.get("MasterOfCar"),
            plate_color=row.get("PlateColor"),
            vehicle_color=row.get("VehicleColor"),
            begin_time=row.get("BeginTime"),
            cancel_time=row.get("CancelTime"),
        ))
    return out
