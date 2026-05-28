"""IncidentRecorder writes pre+post MP4s and attaches them to the packager."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from itips.evidence.recorder import IncidentRecorder


class _FakePackager:
    def __init__(self) -> None:
        self.attached: list[tuple[str, Path, str]] = []

    def attach_file(self, incident_id: str, path: Path, kind: str) -> None:
        self.attached.append((incident_id, Path(path), kind))


def _frame(value: int) -> np.ndarray:
    arr = np.full((180, 320, 3), value, dtype=np.uint8)
    cv2.putText(arr, str(value), (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
    return arr


def test_recorder_writes_pre_and_post_mp4(tmp_path: Path):
    packager = _FakePackager()
    rec = IncidentRecorder(
        camera_id=1, packager=packager,
        pre_event_seconds=2, post_event_seconds=2,
    )
    # Pre-buffer: 10 frames over ~1 second of real time
    for v in range(10):
        rec.feed(_frame(v))
        time.sleep(0.02)

    package_dir = tmp_path / "incident-1"
    package_dir.mkdir()
    rec.begin("incident-1", package_dir)

    # Post-event: another 10 frames
    for v in range(10, 20):
        rec.feed(_frame(v))
        time.sleep(0.02)

    rec.finish(wait_timeout=10.0)

    pre = package_dir / "Video_cam1_pre.mp4"
    post = package_dir / "Video_cam1_post.mp4"
    assert pre.exists() and pre.stat().st_size > 0, "pre.mp4 must be non-empty"
    assert post.exists() and post.stat().st_size > 0, "post.mp4 must be non-empty"

    # Both attached to the packager with the right kinds.
    kinds = {p[2] for p in packager.attached}
    assert {"video_pre", "video_post"}.issubset(kinds)


def test_recorder_handles_finish_without_begin(tmp_path: Path):
    packager = _FakePackager()
    rec = IncidentRecorder(
        camera_id=2, packager=packager,
        pre_event_seconds=1, post_event_seconds=1,
    )
    # Without begin() there's no active recording — finish is a no-op.
    assert rec.finish() is None
    assert packager.attached == []


def test_export_clip_writes_mp4_from_buffer(tmp_path: Path):
    packager = _FakePackager()
    rec = IncidentRecorder(
        camera_id=7, packager=packager,
        pre_event_seconds=30, post_event_seconds=30,
    )
    # Fill the ring buffer with timestamped frames.
    for v in range(20):
        rec.feed(_frame(v))
        time.sleep(0.02)

    out = tmp_path / "clip.mp4"
    # Center on "now" with a wide window so all buffered frames qualify.
    rec.export_clip(out, center_ts=time.monotonic(), pre_seconds=15, post_seconds=15)
    # Encoding runs on a daemon thread — wait for the file to materialise.
    for _ in range(200):
        if out.exists() and out.stat().st_size > 0:
            break
        time.sleep(0.02)
    assert out.exists() and out.stat().st_size > 0


def test_export_clip_no_frames_is_safe(tmp_path: Path):
    packager = _FakePackager()
    rec = IncidentRecorder(
        camera_id=8, packager=packager,
        pre_event_seconds=30, post_event_seconds=30,
    )
    out = tmp_path / "empty.mp4"
    # Buffer empty → no clip written, and definitely no crash.
    rec.export_clip(out, center_ts=time.monotonic(), pre_seconds=15, post_seconds=15)
    time.sleep(0.2)
    assert not out.exists()


def test_post_event_auto_close_after_window(tmp_path: Path):
    packager = _FakePackager()
    rec = IncidentRecorder(
        camera_id=3, packager=packager,
        pre_event_seconds=1, post_event_seconds=0,  # close immediately
    )
    rec.feed(_frame(0))
    package_dir = tmp_path / "incident-3"
    package_dir.mkdir()
    rec.begin("incident-3", package_dir)
    # First feed after begin should self-close because post_seconds == 0
    rec.feed(_frame(1))
    rec.feed(_frame(2))  # should be a no-op
    rec.finish(wait_timeout=5.0)
    post = package_dir / "Video_cam3_post.mp4"
    assert post.exists()  # opened, then released
