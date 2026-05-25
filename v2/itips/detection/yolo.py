"""YOLO11n detector with Ultralytics' native ByteTrack tracker.

Replaces V1's `yolo_engine.py`. Differences:
  - Default model is YOLO11n, not YOLOv8s-seg.
  - Loads a TensorRT engine if the configured path ends in .engine,
    otherwise falls back to a .pt checkpoint and warns.
  - Uses `model.track(..., persist=True, tracker='bytetrack.yaml')` so
    we get ByteTrack IDs for free — V1's centroid tracker is gone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_PERSON_CLASS = 0
_VEHICLE_CLASSES = {1, 2, 3, 5, 7}  # bicycle, car, motorcycle, bus, truck


@dataclass
class Detection:
    class_id: int
    confidence: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    track_id: int | None = None
    mask: np.ndarray | None = None


@dataclass
class YOLOResult:
    detections: list[Detection]
    vehicles: list[Detection]


class YOLOEngine:
    def __init__(
        self,
        *,
        model_path: str,
        fallback_model: str = "yolo11n.pt",
        img_size: int = 640,
        confidence: float = 0.35,
        iou: float = 0.5,
    ) -> None:
        from ultralytics import YOLO

        path = Path(model_path)
        if not path.exists() and not path.name.endswith(".engine"):
            logger.warning("YOLO model %s missing; ultralytics will fetch %s.",
                           model_path, fallback_model)
            model_path = fallback_model
        elif not path.exists():
            logger.warning("TensorRT engine %s missing; falling back to %s",
                           model_path, fallback_model)
            model_path = fallback_model

        self._model = YOLO(model_path)
        self._img_size = img_size
        self._conf = confidence
        self._iou = iou
        self._warmup()
        logger.info("YOLO loaded — model=%s, imgsz=%d, conf=%.2f", model_path, img_size, confidence)

    def _warmup(self) -> None:
        """Run one dummy inference so the lazy fuse() happens in this thread.

        Without this, two camera workers calling `model.track()` simultaneously
        on the first frame race inside Ultralytics' fuse(), and one of them
        crashes with `AttributeError: bn`. Pre-warming serialises the fuse
        before any worker thread exists.
        """
        dummy = np.zeros((self._img_size, self._img_size, 3), dtype=np.uint8)
        self._model.predict(dummy, imgsz=self._img_size, verbose=False)

    def detect(self, frame: np.ndarray, *, camera_id: int) -> YOLOResult:
        results = self._model.track(
            frame,
            imgsz=self._img_size,
            conf=self._conf,
            iou=self._iou,
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )
        detections: list[Detection] = []
        vehicles: list[Detection] = []
        if not results:
            return YOLOResult([], [])

        r = results[0]
        if r.boxes is None:
            return YOLOResult([], [])

        boxes = r.boxes.xyxy.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()
        classes = r.boxes.cls.cpu().numpy().astype(int)
        ids = r.boxes.id.cpu().numpy().astype(int) if r.boxes.id is not None else [None] * len(boxes)

        for box, conf, cls, tid in zip(boxes, confs, classes, ids):
            det = Detection(
                class_id=int(cls),
                confidence=float(conf),
                bbox=tuple(map(float, box)),
                track_id=int(tid) if tid is not None else None,
            )
            if cls == _PERSON_CLASS:
                detections.append(det)
            elif cls in _VEHICLE_CLASSES:
                vehicles.append(det)

        return YOLOResult(detections=detections, vehicles=vehicles)
