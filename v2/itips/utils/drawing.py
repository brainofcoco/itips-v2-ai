"""Drawing helpers for the MJPEG live view.

Slim port of V1 — the full overlay set (zone polygons, face labels,
behaviour-state HUD) lives in V1 and will be brought across as needed.
"""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

from itips.behaviour.zones import get_store

_ZONE_COLOURS = {
    "intrusion": (0, 0, 255),
    "climbing": (0, 165, 255),
    "gate": (0, 255, 255),
    "generator": (255, 0, 255),
}


def draw_zones(
    frame: np.ndarray,
    *,
    camera_id: int,
    preset_id: str = "default",
    zones: dict | None = None,
) -> np.ndarray:
    if zones is None:
        zones = get_store().for_camera_preset(camera_id, preset_id)
    for name, polygon in zones.items():
        if not polygon:
            continue
        pts = np.asarray(polygon, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(frame, [pts], isClosed=True,
                      color=_ZONE_COLOURS.get(name, (200, 200, 200)), thickness=2)
        cv2.putText(frame, name, tuple(pts[0][0]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    _ZONE_COLOURS.get(name, (200, 200, 200)), 2, cv2.LINE_AA)
    return frame


def draw_detections(frame: np.ndarray, detections: Sequence) -> np.ndarray:
    for det in detections:
        x1, y1, x2, y2 = (int(v) for v in det.bbox)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"id{det.track_id} {det.confidence:.2f}" if det.track_id else f"{det.confidence:.2f}"
        cv2.putText(frame, label, (x1, max(0, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    return frame
