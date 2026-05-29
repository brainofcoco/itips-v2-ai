"""PaddleOCR-backed plate reader for cameras without native ANPR.

Lazy-loaded so the v2 baseline doesn't pay for paddleocr on import.
Result routes through AlertEngine.handle_plate_capture — same handler
the native TrafficCarMeasurement path uses.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class PlateEngineUnavailable(RuntimeError):
    """Raised when `paddleocr` / `paddlepaddle` aren't installed.
    Callers catch and degrade to the motion-only / vehicle-gate path."""


# 5–10 alphanum, ≥2 letters AND ≥2 digits — catches Nigerian "LAG-123-XY",
# US "ABC1234", EU "BMW 1234"; rejects pure numbers and street signs.
_PLATE_CHARSET = re.compile(r"[A-Z0-9]")
_PLATE_LEN_RANGE = (5, 10)


@dataclass
class PlateReadResult:
    plate_number: str
    confidence: float
    bbox: tuple[float, float, float, float]
    raw_text: str

    @property
    def display(self) -> str:
        return self.plate_number


class PlateEngine:
    """PaddleOCR-backed plate OCR with a Nigerian-plate-aware filter."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.4,
        use_gpu: bool = False,
        lang: str = "en",
    ) -> None:
        self._min_confidence = float(min_confidence)
        self._use_gpu = bool(use_gpu)
        self._lang = lang
        self._reader = None
        self._init_lock = threading.Lock()
        self._warmup_thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._init_error: Optional[Exception] = None

    # ─── lifecycle ────────────────────────────────────────────────────

    def warmup_async(self) -> None:
        with self._init_lock:
            if self._reader is not None or self._warmup_thread is not None:
                return
            t = threading.Thread(
                target=self._ensure_model_safe, name="plate-engine-warmup", daemon=True,
            )
            self._warmup_thread = t
            t.start()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def _ensure_model_safe(self) -> None:
        try:
            self._ensure_model()
        except PlateEngineUnavailable as exc:
            # Expected on the core image (no ml extras) — degrade to
            # log-only and log one line, not a boot-time traceback.
            with self._init_lock:
                self._init_error = exc
                self._warmup_thread = None
            logger.warning("plate fallback unavailable — %s", exc)
        except Exception as exc:  # noqa: BLE001
            with self._init_lock:
                self._init_error = exc
                # Reset so warmup can be retried after a transient import
                # failure rather than staying not-ready forever.
                self._warmup_thread = None
            logger.exception("PlateEngine warmup failed (will retry on next request)")

    def _ensure_model(self):
        if self._reader is not None:
            return self._reader
        with self._init_lock:
            if self._reader is not None:
                return self._reader
            try:
                from paddleocr import PaddleOCR  # type: ignore
            except ImportError as exc:
                raise PlateEngineUnavailable(
                    "paddleocr is not installed. `pip install itips-ai[ml]` "
                    "or disable the plate fallback in settings."
                ) from exc
            logger.info(
                "PlateEngine: loading PaddleOCR (lang=%s use_gpu=%s)",
                self._lang, self._use_gpu,
            )
            # use_angle_cls handles rotated plates (angled approach);
            # show_log=False silences Paddle's noisy boot output.
            reader = PaddleOCR(
                use_angle_cls=True,
                lang=self._lang,
                use_gpu=self._use_gpu,
                show_log=False,
            )
            self._reader = reader
            self._ready.set()
            logger.info("PlateEngine ready")
        return self._reader

    # ─── reading ──────────────────────────────────────────────────────

    def read_plate(
        self,
        frame: "np.ndarray",
        vehicle_bbox: Optional[tuple[float, float, float, float]] = None,
    ) -> Optional[PlateReadResult]:
        """Find the most plate-like text in `frame` (or its `vehicle_bbox`).

        Returns `None` if no plate-like string crosses the confidence
        and pattern bars. The result's `bbox` is in frame coordinates,
        not crop coordinates — callers can hand it straight to the
        recorder for evidence overlay.
        """
        reader = self._ensure_model()
        crop, (off_x, off_y) = _crop_for_vehicle(frame, vehicle_bbox)
        # Paddle returns [None] (not []) when its detector finds nothing.
        raw = reader.ocr(crop, cls=True)
        detections = (raw[0] if raw else None) or []

        best: Optional[PlateReadResult] = None
        for det in detections:
            text, conf, points = _unpack_paddle_row(det)
            if text is None:
                continue
            cleaned = _clean_plate_text(text)
            if not _looks_like_plate(cleaned):
                continue
            if conf < self._min_confidence:
                continue
            bbox_frame = _points_to_bbox(points, off_x, off_y)
            candidate = PlateReadResult(
                plate_number=cleaned,
                confidence=conf,
                bbox=bbox_frame,
                raw_text=text,
            )
            if best is None or conf > best.confidence:
                best = candidate
        return best


# ─── helpers ─────────────────────────────────────────────────────────


def _clean_plate_text(text: str) -> str:
    return "".join(c for c in text.upper() if _PLATE_CHARSET.match(c))


def _looks_like_plate(cleaned: str) -> bool:
    n = len(cleaned)
    if n < _PLATE_LEN_RANGE[0] or n > _PLATE_LEN_RANGE[1]:
        return False
    letters = sum(1 for c in cleaned if c.isalpha())
    digits = sum(1 for c in cleaned if c.isdigit())
    return letters >= 2 and digits >= 2


def _crop_for_vehicle(
    frame: "np.ndarray",
    bbox: Optional[tuple[float, float, float, float]],
) -> tuple["np.ndarray", tuple[int, int]]:
    if bbox is None:
        return frame, (0, 0)
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    xx1 = int(max(0, x1))
    yy1 = int(max(0, y1))
    xx2 = int(min(w, x2))
    yy2 = int(min(h, y2))
    if xx2 <= xx1 or yy2 <= yy1:
        return frame, (0, 0)
    return frame[yy1:yy2, xx1:xx2], (xx1, yy1)


def _unpack_paddle_row(row):
    """Returns (text, conf, points) or (None, 0.0, None) on a malformed row."""
    if not row or len(row) < 2:
        return None, 0.0, None
    points = row[0]
    text_conf = row[1]
    if not isinstance(text_conf, (list, tuple)) or len(text_conf) < 2:
        return None, 0.0, None
    try:
        return str(text_conf[0]), float(text_conf[1]), points
    except (TypeError, ValueError):
        return None, 0.0, None


def _points_to_bbox(points, off_x: int, off_y: int) -> tuple[float, float, float, float]:
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (
        min(xs) + off_x, min(ys) + off_y,
        max(xs) + off_x, max(ys) + off_y,
    )
