"""YOLOv8 person/vehicle detector for the behavioral fallback.

Lazy-loaded — ultralytics + torch only import on first `detect()`.
Only COCO person + vehicle classes pass through; everything else
is filtered out before the result reaches the behavior engine.
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


# COCO ids: person, bicycle, car, motorcycle, bus, truck.
_TARGET_CLASS_IDS = {0, 1, 2, 3, 5, 7}
_VEHICLE_CLASSES = {"car", "bicycle", "motorcycle", "bus", "truck"}


class ObjectDetector:
    """YOLOv8 wrapped for event-driven detection."""

    def __init__(
        self,
        *,
        model_name: str = "yolov8n.pt",
        confidence: Optional[float] = None,
        imgsz: Optional[int] = None,
        device: Optional[str] = None,
    ) -> None:
        import os
        # On the Jetson, point this at a TensorRT FP16 .engine (faster +
        # lower memory) exported via scripts/export_yolo_tensorrt.sh.
        self._model_name = os.environ.get("ITIPS_BEHAVIOR_MODEL") or model_name
        # Confidence floor and inference resolution. The cameras feed 4K
        # (3840×2160) frames; at YOLO's default imgsz=640 a 4K frame is
        # downscaled ~6× and mid-distance road objects shrink below the
        # detector's reach — which is why crossings on the road camera
        # never fired. Default to 1280 so those objects survive the
        # downscale, and a slightly looser 0.3 confidence for recall.
        # Both env-tunable: drop ITIPS_BEHAVIOR_IMGSZ to 960 if CPU
        # struggles, raise on the Jetson GPU.
        def _envf(name: str, dflt: float) -> float:
            try:
                return float(os.environ.get(name) or dflt)
            except ValueError:
                return dflt
        def _envi(name: str, dflt: int) -> int:
            try:
                return int(os.environ.get(name) or dflt)
            except ValueError:
                return dflt
        self._confidence = float(confidence) if confidence is not None else _envf("ITIPS_BEHAVIOR_CONF", 0.3)
        self._imgsz = int(imgsz) if imgsz is not None else _envi("ITIPS_BEHAVIOR_IMGSZ", 1280)
        # Ultralytics auto-selects CUDA when available; pin manually for JetPack.
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
                # Allow a later retry. The torch/ultralytics import can
                # fail transiently at a busy boot; without resetting the
                # thread handle, warmup_async() would no-op forever and
                # the engine would stay permanently not-ready.
                self._warmup_thread = None
            logger.exception("ObjectDetector warmup failed (will retry on next request)")

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
                "ObjectDetector: loading %s (device=%s, conf>=%.2f, imgsz=%d)",
                self._model_name, self._device or "auto", self._confidence, self._imgsz,
            )
            model = YOLO(self._model_name)
            self._model = model
            self._ready.set()
            logger.info("ObjectDetector ready")
        return self._model

    # ─── inference ────────────────────────────────────────────────────

    def detect(self, frame: "np.ndarray") -> list[Detection]:
        """`frame` is BGR uint8 (OpenCV) — YOLO accepts that natively."""
        model = self._ensure_model()
        kwargs = {"conf": self._confidence, "imgsz": self._imgsz, "verbose": False}
        if self._device is not None:
            kwargs["device"] = self._device
        results = model.predict(frame, **kwargs)
        return self._extract_detections(results)

    def _extract_detections(self, results) -> list[Detection]:
        out: list[Detection] = []
        if not results:
            return out
        res = results[0]
        names = getattr(res, "names", None) or {}
        boxes = getattr(res, "boxes", None)
        if boxes is None:
            return out
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
