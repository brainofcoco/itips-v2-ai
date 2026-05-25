"""GStreamer pipeline string construction.

Isolated from the reader so the pipeline shape is reviewable on its own
and so codec / latency knobs can be tuned without touching reader logic.
"""

from __future__ import annotations


def build_gstreamer_pipeline(rtsp_url: str, codec: str | None = None) -> str:
    """Hardware-accelerated decode → BGR appsink for OpenCV.

    Uses NVDEC (`nvv4l2decoder`) and CUDA colorspace (`nvvidconv`). Drops
    on latency so a slow consumer can't stall the camera. The leaky queue
    between parse and decode keeps cameras independent under back-pressure.
    """
    if codec == "h265":
        depay, parser = "rtph265depay", "h265parse"
    else:
        depay, parser = "rtph264depay", "h264parse"
    return (
        f"rtspsrc location={rtsp_url} protocols=tcp latency=30 drop-on-latency=true "
        f"! {depay} ! {parser} "
        "! queue leaky=downstream max-size-buffers=2 "
        "! nvv4l2decoder "
        "! nvvidconv ! video/x-raw,format=BGRx "
        "! videoconvert ! video/x-raw,format=BGR "
        "! appsink drop=true sync=false max-buffers=1"
    )


def detect_backend() -> str:
    """Return 'gstreamer' if OpenCV was built with it, else 'ffmpeg'."""
    import cv2

    for line in cv2.getBuildInformation().splitlines():
        stripped = line.strip()
        if stripped.startswith("GStreamer:") and "YES" in stripped:
            return "gstreamer"
    return "ffmpeg"
