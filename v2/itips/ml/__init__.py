"""Jetson-side ML fallback layer — engages per camera when
`dahua_health` reports a missing native capability.

Strictly event-driven: nothing here opens an RTSP stream. Heavy deps
live in the `ml` pyproject extra; the baseline runs without them.
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
