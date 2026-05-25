"""Jetson-side ML fallback layer.

Activated per camera when `dahua_health` reports a missing capability.
Strictly event-driven — every entry point here is called from an existing
event handler with a frame the camera already sent. Nothing in this
package opens an RTSP stream or runs a continuous decode loop.

Public surface, in dependency order:

  CapabilityRouter   — answers "does cam N need a fallback for X?"
  EmbeddingStore     — persistent face-embedding DB on the Jetson NVMe
  FaceEngine         — InsightFace SCRFD+ArcFace, lazy-init, event-call

The heavy ML deps (`insightface`, `onnxruntime`) live in the `ml` extra
in pyproject — the core ITIPS-v2 runtime works fine without them and
just disables the fallback. Install with `pip install itips-ai[ml]`.
"""

from itips.ml.behavior_engine import (
    BehaviorAlert,
    BehaviorEngine,
    BehaviorEngineUnavailable,
)
from itips.ml.capability_router import (
    Capability,
    CapabilityRouter,
    CapabilitySnapshot,
)
from itips.ml.embedding_store import EmbeddingRecord, EmbeddingStore
from itips.ml.face_engine import FaceEngine, FaceEngineUnavailable, RecognitionResult
from itips.ml.object_detector import ObjectDetector, ObjectDetectorUnavailable
from itips.ml.plate_engine import PlateEngine, PlateEngineUnavailable, PlateReadResult
from itips.ml.tracker import Detection, IoUTracker, TrackedObject
from itips.ml.zone_store import Zone, ZoneStore

__all__ = [
    "BehaviorAlert",
    "BehaviorEngine",
    "BehaviorEngineUnavailable",
    "Capability",
    "CapabilityRouter",
    "CapabilitySnapshot",
    "Detection",
    "EmbeddingRecord",
    "EmbeddingStore",
    "FaceEngine",
    "FaceEngineUnavailable",
    "IoUTracker",
    "ObjectDetector",
    "ObjectDetectorUnavailable",
    "PlateEngine",
    "PlateEngineUnavailable",
    "PlateReadResult",
    "RecognitionResult",
    "TrackedObject",
    "Zone",
    "ZoneStore",
]
