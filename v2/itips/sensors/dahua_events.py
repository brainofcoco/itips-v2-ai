"""Long-poll listener for a Dahua camera's eventManager stream.

Dahua emits events in two forms over the same `eventManager.cgi?action=attach`
long-poll stream:

1. **Simple line form** (motion, line-cross, region-cross on older firmware):

       Code=VideoMotion;action=Start;index=0
       Code=CrossLineDetection;action=Start;index=0;data={...}

2. **Rich multipart form** (FaceRecognition, FaceDetection, TrafficCarMeasurement,
   FireDetection, SmokeDetection, WorkClothesDetection, …). Each event is one
   multipart part with a `text/plain` body of `Events[N].field=value` lines,
   optionally followed by `image/jpeg` parts carrying the snapshot the camera
   captured at trigger time.

This module parses both. Listeners receive a single `DahuaEvent` dataclass
with the parsed key/value tree and any embedded JPEG. One thread per camera.

The `heartbeat` query param keeps NAT/firewalls from killing the connection
when the camera is idle. On socket drop we reconnect with exponential backoff
so a flaky camera doesn't melt the Jetson.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import requests

from itips.camera.dahua_http import DahuaCameraEndpoint

logger = logging.getLogger(__name__)


# Codes we subscribe to. "All" works on most Dahua firmwares but spams the
# stream; this curated list keeps parsing predictable and CPU low.
DEFAULT_CODES: tuple[str, ...] = (
    # Face — the discriminator between "known worker" and "intruder".
    "FaceDetection",
    "FaceRecognition",
    # Perimeter / region rules (Dahua IVS).
    "CrossLineDetection",
    "CrossRegionDetection",
    "WanderDetection",
    # ANPR.
    "TrafficCarMeasurement",
    "CarDrivingInOut",
    # Life safety.
    "FireDetection",
    "SmokeDetection",
    # Work safety (Phase 2 — log-only for now).
    "WorkClothesDetection",
    # Fallback object detection (camera-side YOLO equivalent).
    "VideoMotion",
)


@dataclass
class DahuaEvent:
    """A single parsed event from the camera's event manager."""

    camera_id: int
    code: str            # e.g. "FaceRecognition"
    action: str          # "Start" | "Stop" | "Pulse"
    index: int           # channel/rule index
    data: dict[str, Any] = field(default_factory=dict)
    jpeg: Optional[bytes] = None
    monotonic_ns: int = 0
    raw_text: str = ""


# ─── parsing primitives ──────────────────────────────────────────────


_SIMPLE_LINE_RE = re.compile(
    r"Code=([^;]+);action=([^;]+);(?:index=(-?\d+))?(.*)$"
)
# Match `Events[N].Some.Nested.Path[M]=value` style.
_FIELD_RE = re.compile(r"Events\[(\d+)\]\.([A-Za-z0-9_.\[\]]+)=(.*)")
# Match `key.with[index].path` segments.
_PATH_SEGMENT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?")


def parse_simple_line(line: str, camera_id: int) -> Optional[DahuaEvent]:
    """Parse a single `Code=X;action=Y;index=N;data={...}` line."""
    m = _SIMPLE_LINE_RE.match(line.strip())
    if not m:
        return None
    code, action, idx, tail = m.groups()
    data: dict[str, Any] = {}
    if "data=" in tail:
        try:
            data = json.loads(tail.split("data=", 1)[1])
        except (ValueError, IndexError):
            data = {}
    return DahuaEvent(
        camera_id=camera_id,
        code=code.strip(),
        action=action.strip(),
        index=int(idx) if idx else 0,
        data=data,
        monotonic_ns=time.monotonic_ns(),
        raw_text=line,
    )


def parse_multipart_text(text: str, camera_id: int) -> list[DahuaEvent]:
    """Parse a `text/plain` part containing one or more `Events[N]....` blocks.

    Multiple events can ride the same part — the camera batches when several
    rules trigger in quick succession.
    """
    aggregates: dict[int, dict[str, Any]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _FIELD_RE.match(line)
        if not m:
            continue
        idx, path, value = int(m.group(1)), m.group(2), m.group(3).strip()
        bucket = aggregates.setdefault(idx, {})
        _assign(bucket, path, _coerce(value))

    out: list[DahuaEvent] = []
    for idx in sorted(aggregates.keys()):
        body = aggregates[idx]
        base = body.get("EventBaseInfo", {}) or {}
        code = base.get("Code") or body.pop("Code", None)
        action = base.get("Action") or body.pop("Action", "Pulse")
        sub_index = base.get("Index") or body.pop("Index", 0)
        if not code:
            continue
        out.append(DahuaEvent(
            camera_id=camera_id,
            code=str(code),
            action=str(action),
            index=int(sub_index or 0),
            data=body,
            monotonic_ns=time.monotonic_ns(),
            raw_text=text,
        ))
    return out


def _assign(target: dict, path: str, value: Any) -> None:
    """Walk a dotted/indexed path and set value at the leaf."""
    segments = list(_PATH_SEGMENT_RE.finditer(path))
    if not segments:
        return
    cursor: Any = target
    for i, m in enumerate(segments):
        name = m.group(1)
        sub_idx = m.group(2)
        is_last = i == len(segments) - 1
        if sub_idx is None:
            if is_last:
                cursor[name] = value
            else:
                cursor = cursor.setdefault(name, {})
        else:
            sub_idx_int = int(sub_idx)
            arr = cursor.setdefault(name, [])
            while len(arr) <= sub_idx_int:
                arr.append({})
            if is_last:
                arr[sub_idx_int] = value
            else:
                if not isinstance(arr[sub_idx_int], dict):
                    arr[sub_idx_int] = {}
                cursor = arr[sub_idx_int]


def _coerce(value: str) -> Any:
    """Best-effort string → int/float/bool/string."""
    if value == "":
        return ""
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    # Bracket-list form `[1,2,3]` — Dahua uses it for BoundingBox etc.
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except ValueError:
            pass
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


# ─── stream consumer ─────────────────────────────────────────────────


class _MultipartStream:
    """Read a `multipart/x-mixed-replace` byte stream into discrete parts.

    Dahua does not always send Content-Length, so we boundary-scan over a
    growing buffer. Each yielded tuple is (headers_dict, body_bytes).
    """

    def __init__(self, raw_stream, boundary: bytes) -> None:
        self._raw = raw_stream
        self._boundary = b"--" + boundary
        self._buffer = b""

    def __iter__(self):
        return self

    def __next__(self) -> tuple[dict[str, str], bytes]:
        while True:
            chunk = self._raw.read(4096)
            if not chunk:
                if not self._buffer:
                    raise StopIteration
                # Last part with no trailing boundary.
                part = self._buffer
                self._buffer = b""
                return self._parse_part(part)
            self._buffer += chunk
            # Skip to the first boundary if we haven't seen one yet.
            first = self._buffer.find(self._boundary)
            if first < 0:
                continue
            if first > 0:
                # Discard preamble.
                self._buffer = self._buffer[first:]
            # We're now positioned at a boundary. Look for the next one.
            after_first = len(self._boundary)
            next_idx = self._buffer.find(self._boundary, after_first)
            if next_idx < 0:
                continue
            part = self._buffer[after_first:next_idx]
            self._buffer = self._buffer[next_idx:]
            # Strip leading CRLF after the boundary line.
            part = part.lstrip(b"\r\n")
            return self._parse_part(part)

    @staticmethod
    def _parse_part(part: bytes) -> tuple[dict[str, str], bytes]:
        sep = part.find(b"\r\n\r\n")
        if sep < 0:
            sep = part.find(b"\n\n")
            head_end = sep + 2 if sep >= 0 else len(part)
        else:
            head_end = sep + 4
        head_bytes = part[:sep] if sep >= 0 else b""
        body = part[head_end:] if sep >= 0 else b""
        # Trim trailing CRLF that often precedes the next boundary.
        if body.endswith(b"\r\n"):
            body = body[:-2]
        headers: dict[str, str] = {}
        for raw in head_bytes.decode("utf-8", errors="replace").splitlines():
            if ":" not in raw:
                continue
            k, _, v = raw.partition(":")
            headers[k.strip().lower()] = v.strip()
        return headers, body


# ─── listener ────────────────────────────────────────────────────────


class DahuaEventListener(threading.Thread):
    """Per-camera long-poll subscriber to `/cgi-bin/eventManager.cgi`.

    Run as a daemon — `stop()` to shut down cleanly. All exceptions are
    caught inside the run loop so a flaky camera can never crash the
    worker thread.
    """

    def __init__(
        self,
        endpoint: DahuaCameraEndpoint,
        camera_id: int,
        on_event: Callable[[DahuaEvent], None],
        *,
        codes: tuple[str, ...] = DEFAULT_CODES,
        heartbeat_s: int = 10,
        max_backoff_s: float = 30.0,
    ) -> None:
        super().__init__(name=f"dahua-events-{camera_id}", daemon=True)
        self._endpoint = endpoint
        self._camera_id = camera_id
        self._on_event = on_event
        self._codes = codes
        self._heartbeat_s = heartbeat_s
        self._max_backoff_s = max_backoff_s
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        codes_str = ",".join(self._codes)
        params = {
            "action": "attach",
            "codes": f"[{codes_str}]",
            "heartbeat": self._heartbeat_s,
        }
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with self._endpoint.get(
                    "/cgi-bin/eventManager.cgi",
                    params=params,
                    timeout=(5, self._heartbeat_s * 3),
                    stream=True,
                ) as r:
                    if r.status_code != 200:
                        logger.warning(
                            "cam %d events: HTTP %d, retrying",
                            self._camera_id, r.status_code,
                        )
                        self._sleep_backoff(backoff)
                        backoff = min(backoff * 2, self._max_backoff_s)
                        continue
                    backoff = 1.0
                    logger.info(
                        "cam %d event stream connected (%s)",
                        self._camera_id, self._endpoint.safe_label(),
                    )
                    self._consume(r)
            except requests.RequestException as exc:
                if self._stop.is_set():
                    return
                logger.warning(
                    "cam %d event stream dropped (%s); reconnecting in %.1fs",
                    self._camera_id, exc, backoff,
                )
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._max_backoff_s)
            except Exception:
                logger.exception("cam %d event loop unexpected error", self._camera_id)
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._max_backoff_s)

    # ─── consumption ──────────────────────────────────────────────────

    def _consume(self, response: requests.Response) -> None:
        """Dispatch to the multipart or line-based parser based on headers."""
        content_type = response.headers.get("Content-Type", "")
        boundary = _extract_boundary(content_type)
        if boundary:
            self._consume_multipart(response, boundary.encode("ascii"))
        else:
            self._consume_lines(response)

    def _consume_lines(self, response: requests.Response) -> None:
        for raw in response.iter_lines():
            if self._stop.is_set():
                return
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            if "Heartbeat" in raw:
                continue
            event = parse_simple_line(raw, self._camera_id)
            if event is not None:
                self._dispatch(event)

    def _consume_multipart(self, response: requests.Response, boundary: bytes) -> None:
        stream = _MultipartStream(response.raw, boundary)
        pending: Optional[DahuaEvent] = None
        for headers, body in stream:
            if self._stop.is_set():
                return
            content_type = headers.get("content-type", "").lower()
            if "text/plain" in content_type or not content_type:
                text = body.decode("utf-8", errors="replace")
                if "Heartbeat" in text and "Events[" not in text:
                    continue
                events = parse_multipart_text(text, self._camera_id)
                if not events:
                    # Some firmwares still send the simple Code= form inside
                    # a multipart part — try line parsing.
                    for line in text.splitlines():
                        ev = parse_simple_line(line, self._camera_id)
                        if ev is not None:
                            events.append(ev)
                if not events:
                    continue
                # Flush any prior event (no JPEG arrived for it).
                if pending is not None:
                    self._dispatch(pending)
                # Hold the LAST event so a following image part can attach.
                for ev in events[:-1]:
                    self._dispatch(ev)
                pending = events[-1]
            elif "image/jpeg" in content_type:
                if pending is not None:
                    pending.jpeg = body
                    self._dispatch(pending)
                    pending = None
            else:
                # Unknown part type — ignore.
                continue
        if pending is not None:
            self._dispatch(pending)

    def _dispatch(self, event: DahuaEvent) -> None:
        try:
            self._on_event(event)
        except Exception:
            logger.exception(
                "cam %d on_event handler crashed on %s/%s",
                self._camera_id, event.code, event.action,
            )

    def _sleep_backoff(self, seconds: float) -> None:
        self._stop.wait(timeout=seconds)


def _extract_boundary(content_type: str) -> Optional[str]:
    if "multipart" not in content_type.lower():
        return None
    for token in content_type.split(";"):
        token = token.strip()
        if token.lower().startswith("boundary="):
            value = token.split("=", 1)[1].strip()
            return value.strip('"')
    return None
