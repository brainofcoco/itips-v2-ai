"""YOLO11 inference via onnxruntime-gpu only — no PyTorch dependency.

V2 default loads ultralytics + PyTorch for YOLO, which costs ~2 GB on
Jetson L4T (PyTorch CUDA caching allocator + cuDNN workspace). This
engine is a drop-in replacement that loads a pre-exported `.onnx`
model and runs inference through `onnxruntime-gpu` — already required
for InsightFace, so adding this path costs ~0 MB extra.

Trade-offs vs. the ultralytics path:

  * No ByteTrack. The `.track_id` field on every Detection is None,
    which means BehaviourAnalyser loses temporal continuity for
    loitering/zone-dwell logic. For `ITIPS_CAMERA_MODE=event_driven`
    this is fine — there's no continuous tracking to begin with.
    For streaming mode, prefer the ultralytics backend.
  * No segmentation masks. We only do detection; segmentation isn't
    used anywhere in V2 today.
  * Post-processing (anchor decode + NMS) is in numpy + cv2.dnn.NMSBoxes
    instead of torchvision's NMS — slightly slower but no torch import.

Memory profile on Jetson L4T (measured on Orin Nano, idle steady-state):

  * Ultralytics path: ~3-4 GB GPU+host (torch + cuDNN workspace + cache)
  * This ONNX path:    ~0.5-1 GB     (just ONNX RT's CUDA EP, shared
                                       with the face engine)

See `docs/jetson-memory.md` for full context.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from itips.detection.yolo import (
    Detection,
    YOLOResult,
    _PERSON_CLASS,
    _VEHICLE_CLASSES,
)

logger = logging.getLogger(__name__)


class OnnxYOLOEngine:
    """Drop-in replacement for YOLOEngine that uses only onnxruntime-gpu."""

    def __init__(
        self,
        *,
        model_path: str,
        fallback_model: str = "yolo11n.onnx",
        img_size: int = 640,
        confidence: float = 0.35,
        iou: float = 0.5,
    ) -> None:
        # Local import — we want this whole module importable even in unit
        # test environments where onnxruntime isn't installed.
        import onnxruntime as ort

        path = Path(model_path)
        if not path.exists():
            fb = Path(fallback_model)
            if fb.exists():
                logger.warning(
                    "YOLO ONNX %s missing; using fallback %s", model_path, fallback_model
                )
                path = fb
            else:
                raise FileNotFoundError(
                    f"YOLO ONNX model not found at {model_path}. "
                    "Run scripts/export_yolo_onnx.sh first to convert the .pt to .onnx."
                )

        # CUDAExecutionProvider first; CPU fallback so unit tests on a
        # GPU-less laptop still work.
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        so.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(path), sess_options=so, providers=providers
        )
        self._input_name = self._session.get_inputs()[0].name
        self._img_size = img_size
        self._conf = confidence
        self._iou = iou

        active_providers = self._session.get_providers()
        logger.info(
            "OnnxYOLOEngine loaded — model=%s, imgsz=%d, conf=%.2f, providers=%s",
            path, img_size, confidence, active_providers,
        )
        if "CUDAExecutionProvider" not in active_providers:
            logger.warning("YOLO ONNX is running on CPU only — performance will be poor.")

    # --------------------------------------------------------- public API

    def detect(self, frame: np.ndarray, *, camera_id: int) -> YOLOResult:
        """Run inference on a single BGR frame. Returns persons + vehicles."""
        h0, w0 = frame.shape[:2]
        tensor, scale, pad_x, pad_y = self._preprocess(frame)
        outputs = self._session.run(None, {self._input_name: tensor})
        return self._postprocess(outputs[0], h0, w0, scale, pad_x, pad_y)

    # --------------------------------------------------------- internals

    def _preprocess(self, frame: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        """Letterbox to img_size, BGR→RGB, normalize, HWC→NCHW.

        Returns the input tensor plus the geometry needed to unproject
        boxes back into the original frame coordinate space.
        """
        h, w = frame.shape[:2]
        scale = min(self._img_size / h, self._img_size / w)
        new_h, new_w = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        pad_x = (self._img_size - new_w) // 2
        pad_y = (self._img_size - new_h) // 2

        # YOLO's standard letterbox pad colour.
        padded = np.full((self._img_size, self._img_size, 3), 114, dtype=np.uint8)
        padded[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        chw = rgb.transpose(2, 0, 1).astype(np.float32) * (1.0 / 255.0)
        return chw[None, ...], scale, pad_x, pad_y

    def _postprocess(
        self,
        output: np.ndarray,
        h0: int,
        w0: int,
        scale: float,
        pad_x: int,
        pad_y: int,
    ) -> YOLOResult:
        """Decode YOLO11 anchor output (1, 4+nc, num_anchors) → Detections."""
        # YOLO11 ONNX export shape is (1, 84, N) for COCO 80 classes:
        #   rows 0..3   = cx, cy, w, h (in img_size pixel coords)
        #   rows 4..83  = per-class scores
        preds = output[0].T  # (N, 84)
        boxes = preds[:, :4]
        class_scores = preds[:, 4:]

        max_scores = class_scores.max(axis=1)
        class_ids = class_scores.argmax(axis=1)

        keep_mask = max_scores >= self._conf
        if not keep_mask.any():
            return YOLOResult([], [])
        boxes = boxes[keep_mask]
        scores = max_scores[keep_mask]
        class_ids = class_ids[keep_mask]

        # cx,cy,w,h → x1,y1,x2,y2 still in letterboxed 640 px space.
        x1 = boxes[:, 0] - boxes[:, 2] * 0.5
        y1 = boxes[:, 1] - boxes[:, 3] * 0.5
        x2 = boxes[:, 0] + boxes[:, 2] * 0.5
        y2 = boxes[:, 1] + boxes[:, 3] * 0.5

        # NMS via cv2 — input format is x,y,w,h (not x1,y1,x2,y2).
        nms_boxes = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1).astype(np.float32)
        idxs = cv2.dnn.NMSBoxes(
            nms_boxes.tolist(),
            scores.astype(np.float32).tolist(),
            score_threshold=float(self._conf),
            nms_threshold=float(self._iou),
        )
        if len(idxs) == 0:
            return YOLOResult([], [])
        idxs = np.asarray(idxs).flatten()

        xyxy = np.stack([x1[idxs], y1[idxs], x2[idxs], y2[idxs]], axis=1)
        scores = scores[idxs]
        class_ids = class_ids[idxs]

        # Undo letterbox padding + scale back to original frame coordinates.
        xyxy[:, [0, 2]] -= pad_x
        xyxy[:, [1, 3]] -= pad_y
        xyxy /= scale
        xyxy[:, [0, 2]] = np.clip(xyxy[:, [0, 2]], 0, w0 - 1)
        xyxy[:, [1, 3]] = np.clip(xyxy[:, [1, 3]], 0, h0 - 1)

        detections: list[Detection] = []
        vehicles: list[Detection] = []
        for (x1f, y1f, x2f, y2f), conf, cls in zip(xyxy, scores, class_ids):
            det = Detection(
                class_id=int(cls),
                confidence=float(conf),
                bbox=(float(x1f), float(y1f), float(x2f), float(y2f)),
                track_id=None,  # ByteTrack unavailable in ONNX-only path.
            )
            if cls == _PERSON_CLASS:
                detections.append(det)
            elif cls in _VEHICLE_CLASSES:
                vehicles.append(det)
        return YOLOResult(detections=detections, vehicles=vehicles)
