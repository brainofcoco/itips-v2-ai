"""Registrator should recover a known synthetic transform within tolerance."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from itips.behaviour.registration import (
    FrameRegistrator,
    transform_polygon,
    transform_zones,
)


def _feature_rich_frame(width: int = 640, height: int = 360) -> np.ndarray:
    """Build a frame with enough texture for ORB to lock onto.

    A plain gradient or noise field can starve ORB of corners; we draw
    a grid of varied rectangles + circles + diagonal lines so the
    feature detector has unambiguous keypoints.
    """
    rng = np.random.default_rng(seed=42)
    frame = (rng.integers(20, 80, (height, width, 3), dtype=np.uint8))
    for x in range(40, width, 60):
        for y in range(40, height, 60):
            colour = tuple(int(c) for c in rng.integers(80, 255, 3))
            cv2.rectangle(frame, (x, y), (x + 25, y + 18), colour, -1)
            cv2.circle(frame, (x + 12, y + 9), 4, (0, 0, 0), 1)
    for i in range(8):
        cv2.line(frame, (i * 80, 0), (i * 80 + 40, height),
                 (200, 200, 200), 1)
    return frame


def _write_reference(tmp_path: Path, frame: np.ndarray, camera_id: int, preset_id: str) -> Path:
    path = tmp_path / f"cam{camera_id}_{preset_id}.jpg"
    cv2.imwrite(str(path), frame)
    return path


def test_registration_recovers_translation(tmp_path: Path):
    ref = _feature_rich_frame()
    _write_reference(tmp_path, ref, camera_id=1, preset_id="home")

    # Shift the live frame right and down by a known amount.
    dx, dy = 40, 25
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    h, w = ref.shape[:2]
    cur = cv2.warpAffine(ref, M, (w, h), borderValue=(30, 30, 30))

    reg = FrameRegistrator(camera_id=1, references_root=tmp_path)
    H = reg.compute_homography(cur, preset_id="home")
    assert H is not None, "registration should succeed on a synthetic translation"

    # Project a polygon from reference → live; it should be shifted by (dx, dy).
    polygon = [(100.0, 100.0), (200.0, 100.0), (200.0, 180.0), (100.0, 180.0)]
    projected = transform_polygon(polygon, H)
    for (rx, ry), (px, py) in zip(polygon, projected):
        assert abs(px - (rx + dx)) < 2.5, f"x shift off: {px - rx} vs {dx}"
        assert abs(py - (ry + dy)) < 2.5, f"y shift off: {py - ry} vs {dy}"


def test_registration_returns_none_without_reference(tmp_path: Path):
    reg = FrameRegistrator(camera_id=2, references_root=tmp_path)
    cur = _feature_rich_frame()
    assert reg.compute_homography(cur, preset_id="home") is None


def test_transform_zones_identity_passthrough():
    zones = {"a": [(0, 0), (10, 0), (10, 10), (0, 10)]}
    out = transform_zones(zones, None)
    assert out == {"a": [(0, 0), (10, 0), (10, 10), (0, 10)]}


def test_reference_reloads_on_mtime_change(tmp_path: Path, monkeypatch):
    """Bumping the reference file should cause the next call to re-load."""
    ref1 = _feature_rich_frame()
    path = _write_reference(tmp_path, ref1, camera_id=3, preset_id="home")
    reg = FrameRegistrator(camera_id=3, references_root=tmp_path)
    loaded1 = reg.current_for("home")
    assert loaded1 is not None
    first_mtime = loaded1.mtime_ns

    # Re-write with a different image and bump mtime forward.
    ref2 = _feature_rich_frame()  # same RNG seed → same content; we just need a new mtime
    cv2.imwrite(str(path), ref2)
    import os
    os.utime(path, ns=(first_mtime + 10_000_000, first_mtime + 10_000_000))
    loaded2 = reg.current_for("home")
    assert loaded2 is not None and loaded2.mtime_ns != first_mtime
