"""Software PTZ — digital crop driven by preset (pan, tilt, zoom).

The virtual PTZ takes a wide-source frame and returns a smaller window
that simulates the view of a physical PTZ pointed at a named preset.
Downstream code (YOLO, behaviour, drawing, the MJPEG feed, the dashboard
snapshot endpoint) all see the *same* cropped frame, so polygons drawn
on the dashboard line up with what the analyser checks.

Used only when no real PTZ is attached. With real PTZ hardware this
module is bypassed and the physical camera does the work.
"""

from __future__ import annotations

import cv2
import numpy as np

from itips.runtime.presets import PresetParams


def apply(frame: np.ndarray, params: PresetParams) -> np.ndarray:
    """Return a cropped + resized frame matching the preset window.

    The output frame has the same shape as the input so consumers can
    treat the result as a drop-in replacement.

    `zoom` clamps to [1.0, 8.0] to avoid degenerate crops; `pan` and
    `tilt` clamp to [-1, 1] before the crop window is computed.
    """
    if frame is None:
        return frame
    h, w = frame.shape[:2]
    pan = max(-1.0, min(1.0, params.pan))
    tilt = max(-1.0, min(1.0, params.tilt))
    zoom = max(1.0, min(8.0, params.zoom))

    crop_w = max(1, int(round(w / zoom)))
    crop_h = max(1, int(round(h / zoom)))

    cx = int(round(w / 2 + pan * (w - crop_w) / 2))
    cy = int(round(h / 2 + tilt * (h - crop_h) / 2))

    x1 = max(0, min(w - crop_w, cx - crop_w // 2))
    y1 = max(0, min(h - crop_h, cy - crop_h // 2))
    cropped = frame[y1:y1 + crop_h, x1:x1 + crop_w]

    if cropped.shape[0] != h or cropped.shape[1] != w:
        cropped = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
    return cropped
