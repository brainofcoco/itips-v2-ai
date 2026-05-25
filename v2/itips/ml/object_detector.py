"""YOLOv8 person/vehicle detector for the behavioral fallback.

Same lazy-init posture as `face_engine` and `plate_engine`: nothing
imports `ultralytics` (or torch) at module load. The first call to
`detect()` triggers the model load. A `warmup_async()` kicks it off
in the background at boot.

Only two object classes are interesting for ITIPS today:
  * `person`  (COCO class 0) — drives intrusion / loitering / line
                                crossing in the absence of native IVS.
  * `car`     (COCO class 2) — informs the plate fallback's vehicle
                                bbox argument (Phase 2 currently uses
                                whole-frame OCR; this lets us tighten
                                it later without re-architecting).

Other COCO classes are filtered out before the result reaches the
behavior engine — keeps the alert surface focused on what cameras
without native IVS actually need to detect.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Optional

from itips.ml.tracker import Detection

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class ObjectDetectorUnavailable(RuntimeError):
    """Raised when `ultralytics` / `torch` aren't installed."""


# COCO class IDs we care about. The string names come out of
# Ultralytics' `model.names` and become the `class_name` carried in
# `Detection` records.
_TARGET_CLASS_IDS = {0, 1, 2, 3, 5, 7}  # person, bicycle, car, motorcycle, bus, truck
_VEHICLE_CLASSES = {"car", "bicycle", "motorcycle", "bus", "truck"}


class ObjectDetector:
    """YOLOv8 wrapped for event-driven detection."""

    def __init__(
        self,
        *,
        model_name: str = "yolov8n.pt",
        confidence: float = 0.4,
        device: Optional[str] = None,
    ) -> None:
        self._model_name = model_name
        self._confidence = float(confidence)
        # Ultralytics auto-selects CUDA when available; pass an explicit
        # device only if the caller (e.g. JetPack init script) wants to
        # pin to a specific GPU.
        self._device = device
        self._model = None
        self._init_lock = threading.Lock()
        self._warmup_thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._init_error: Optional[Exception] = None

    # ─── lifecycle ────────────────────────────────────────────────────

    def warmup_async(self) -> None:
        with self._init_lock:
            if self._model is not None or self._warmup_thread is not None:
                return
            t = threading.Thread(
                target=self._ensure_model_safe, name="object-detector-warmup", daemon=True,
            )
            self._warmup_thread = t
            t.start()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def _ensure_model_safe(self) -> None:
        try:
            self._ensure_model()
        except Exception as exc:  # noqa: BLE001
            with self._init_lock:
                self._init_error = exc
            logger.exception("ObjectDetector warmup failed")

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        with self._init_lock:
            if self._model is not None:
                return self._model
            try:
                from ultralytics import YOLO  # type: ignore
            except ImportError as exc:
                raise ObjectDetectorUnavailable(
                    "ultralytics is not installed. `pip install itips-ai[ml]` "
                    "or disable the behavior fallback in settings."
                ) from exc
            logger.info(
                "ObjectDetector: loading %s (device=%s, conf>=%.2f)",
                self._model_name, self._device or "auto", self._confidence,
            )
            model = YOLO(self._model_name)
            self._model = model
            self._ready.set()
            logger.info("ObjectDetector ready")
        return self._model

    # ─── inference ────────────────────────────────────────────────────

    def detect(self, frame: "np.ndarray") -> list[Detection]:
        """Run a single forward pass; return filtered Detections.

        Frame is BGR uint8 (OpenCV format — same as `event.jpeg` →
        `cv2.imdecode` produces). YOLO accepts that natively.
        """
        model = self._ensure_model()
        # `verbose=False` keeps the Jetson log quiet; `conf` filters
        # at the predict step so we don't allocate tensors we'll just
        # drop. Stream API would be wasteful for a single image.
        kwargs = {"conf": self._confidence, "verbose": False}
        if self._device is not None:
            kwargs["device"] = self._device
        results = model.predict(frame, **kwargs)
        return self._extract_detections(results)

    def _extract_detections(self, results) -> list[Detection]:
        out: list[Detection] = []
        # Ultralytics returns a list of Results, one per input image —
        # we always send a single image, so results[0] is the one.
        if not results:
            return out
        res = results[0]
        names = getattr(res, "names", None) or {}
        boxes = getattr(res, "boxes", None)
        if boxes is None:
            return out
        # `.boxes` exposes `.xyxy` (tensor), `.cls`, `.conf`.
        for box in boxes:
            cls_id = int(box.cls.item()) if hasattr(box.cls, "item") else int(box.cls[0])
            if cls_id not in _TARGET_CLASS_IDS:
                continue
            class_name = str(names.get(cls_id, str(cls_id)))
            conf = float(box.conf.item()) if hasattr(box.conf, "item") else float(box.conf[0])
            xyxy = box.xyxy[0]
            x1, y1, x2, y2 = (float(xyxy[0]), float(xyxy[1]),
                              float(xyxy[2]), float(xyxy[3]))
            out.append(Detection(
                bbox=(x1, y1, x2, y2),
                class_name=class_name,
                confidence=conf,
            ))
        return out


def is_vehicle(class_name: str) -> bool:
    return class_name in _VEHICLE_CLASSES


def is_person(class_name: str) -> bool:
    return class_name == "person"
