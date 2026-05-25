"""World-anchor zones via per-frame homography against a reference.

When a polygon is drawn on the dashboard, its coordinates are saved in
the *reference frame's* pixel space — the exact frame the user drew on.
As the live camera drifts or pans, this module computes a homography
mapping reference→current, so the polygon can be re-projected onto each
new frame and stays locked to the ground.

ORB + BFMatcher + RANSAC is fast enough for one camera per CPU thread
(~5–10 ms per frame at 1280×720). If matching fails (low light, sudden
motion, featureless scene) we return identity and the analyser falls
back to today's screen-anchored behaviour.

The reference image is persisted under
`/opt/itips/var/references/cam{N}_{preset}.jpg` so it survives container
restarts. The registrator reloads automatically when the file's mtime
changes — written by the dashboard whenever zones are saved.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_MIN_GOOD_MATCHES = 12
_RATIO_TEST = 0.75


@dataclass
class _Reference:
    path: Path
    mtime_ns: int
    gray: np.ndarray
    keypoints: list
    descriptors: np.ndarray


def reference_path(root: Path, camera_id: int, preset_id: str) -> Path:
    return Path(root) / f"cam{camera_id}_{preset_id}.jpg"


class FrameRegistrator:
    """Per-camera homography solver between a stored reference and live frames.

    `current_for(preset_id)` reloads the reference for that preset if the
    file mtime has changed; returns the active reference or None.
    `compute_homography(frame)` returns a 3×3 numpy array mapping points
    in the reference frame to their projected location in `frame`, or
    None if registration failed.
    """

    def __init__(self, camera_id: int, references_root: Path) -> None:
        self.camera_id = camera_id
        self._root = Path(references_root)
        self._orb = cv2.ORB_create(nfeatures=1500)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        self._lock = threading.Lock()
        self._current: Optional[_Reference] = None
        self._current_preset: Optional[str] = None
        self._warn_throttle = 0

    # ─── reference loading ────────────────────────────────────────

    def current_for(self, preset_id: str) -> Optional[_Reference]:
        path = reference_path(self._root, self.camera_id, preset_id)
        with self._lock:
            if self._current_preset != preset_id:
                self._current_preset = preset_id
                self._current = None
            if not path.exists():
                if self._current is not None:
                    logger.info("Camera %d: reference for '%s' removed", self.camera_id, preset_id)
                    self._current = None
                return None
            mtime_ns = path.stat().st_mtime_ns
            if self._current is not None and self._current.mtime_ns == mtime_ns:
                return self._current
            ref = self._load(path, mtime_ns)
            self._current = ref
            return ref

    def _load(self, path: Path, mtime_ns: int) -> Optional[_Reference]:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            logger.warning("Camera %d: reference %s could not be decoded", self.camera_id, path)
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        keypoints, descriptors = self._orb.detectAndCompute(gray, None)
        if descriptors is None or len(keypoints) < _MIN_GOOD_MATCHES:
            logger.warning("Camera %d: reference %s has too few features (%d)",
                           self.camera_id, path, 0 if descriptors is None else len(keypoints))
            return None
        logger.info("Camera %d: loaded reference %s (%d features)",
                    self.camera_id, path.name, len(keypoints))
        return _Reference(path=path, mtime_ns=mtime_ns, gray=gray,
                          keypoints=keypoints, descriptors=descriptors)

    # ─── homography ───────────────────────────────────────────────

    def compute_homography(self, frame: np.ndarray, preset_id: str) -> Optional[np.ndarray]:
        ref = self.current_for(preset_id)
        if ref is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        cur_kp, cur_des = self._orb.detectAndCompute(gray, None)
        if cur_des is None or len(cur_kp) < _MIN_GOOD_MATCHES:
            self._warn("frame features too few (%d)", 0 if cur_des is None else len(cur_kp))
            return None
        try:
            knn = self._matcher.knnMatch(ref.descriptors, cur_des, k=2)
        except cv2.error:
            self._warn("knnMatch failed")
            return None
        good = []
        for pair in knn:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < _RATIO_TEST * n.distance:
                good.append(m)
        if len(good) < _MIN_GOOD_MATCHES:
            self._warn("only %d good matches", len(good))
            return None
        src = np.float32([ref.keypoints[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([cur_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None or mask is None or int(mask.sum()) < _MIN_GOOD_MATCHES:
            self._warn("RANSAC rejected too many matches")
            return None
        return H

    def _warn(self, msg: str, *args) -> None:
        # Rate-limit: log every 60th failure so we don't drown the log.
        self._warn_throttle += 1
        if self._warn_throttle % 60 == 1:
            logger.warning("Camera %d registration: " + msg, self.camera_id, *args)


# ─── geometry helpers ────────────────────────────────────────────────


def transform_polygon(polygon: list, H: np.ndarray) -> list[tuple[float, float]]:
    """Project a polygon (in reference coords) into current-frame coords."""
    if not polygon:
        return []
    pts = np.asarray(polygon, dtype=np.float32).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(pts, H).reshape(-1, 2)
    return [(float(x), float(y)) for x, y in projected]


def transform_zones(
    zones: dict[str, list],
    H: Optional[np.ndarray],
) -> dict[str, list[tuple[float, float]]]:
    """Transform a full zone dict; identity passthrough when H is None."""
    if H is None:
        return {name: [tuple(p) for p in polygon] for name, polygon in zones.items()}
    return {name: transform_polygon(polygon, H) for name, polygon in zones.items()}
